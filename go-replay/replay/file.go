package replay

import (
	"bufio"
	"bytes"
	"compress/gzip"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"slices"
	"time"

	"github.com/andybalholm/brotli"
)

const HeaderSize = 0x410
const DefaultMaxPayload = 256 << 20

type Header struct {
	Version string `json:"clientVersion"`
	Raw     Bytes  `json:"raw"`
}
type Record struct {
	ID      int      `json:"id"`
	Offset  int64    `json:"offset"`
	Kind    uint16   `json:"kind"`
	Version uint16   `json:"version"`
	Tick    uint32   `json:"tick"`
	Length  uint32   `json:"length"`
	Aux     uint32   `json:"aux"`
	Payload Bytes    `json:"rawPayload,omitempty"`
	Status  string   `json:"status"`
	Error   string   `json:"error,omitempty"`
	Packets []Packet `json:"packets"`
}
type Packet struct {
	Ordinal    int     `json:"ordinal"`
	Category   string  `json:"category"`
	PacketType *int32  `json:"packetType"`
	Name       string  `json:"name"`
	Target     *uint32 `json:"target,omitempty"`
	UserID     *int64  `json:"userId,omitempty"`
	ItemBoxID  *int32  `json:"itemBoxObjectId,omitempty"`
	Raw        Bytes   `json:"raw"`
	Status     string  `json:"status"`
	Error      string  `json:"error,omitempty"`
	Value      any     `json:"value"`
}
type Summary struct {
	Format                     string             `json:"format"`
	Version                    string             `json:"clientVersion"`
	SourceSHA256               string             `json:"sourceSha256"`
	RecordCount                int                `json:"recordCount"`
	PacketCount                int                `json:"packetCount"`
	StatusCounts               map[string]int     `json:"decodeStatusCounts"`
	TypeCounts                 map[string]int     `json:"packetTypeCounts"`
	Complete                   bool               `json:"complete"`
	SemanticCompletenessProven bool               `json:"semanticCompletenessProven"`
	Started                    string             `json:"started"`
	Finished                   string             `json:"finished"`
	Seconds                    float64            `json:"seconds"`
	PhaseSeconds               map[string]float64 `json:"phaseSeconds"`
	FatalError                 string             `json:"fatalError,omitempty"`
}
type Options struct {
	SchemaDirectory string
	MaxPayload      int
	KeepRawRecords  bool
}
type definition struct {
	Name       string `json:"name"`
	Base       string `json:"baseType"`
	PacketType int32  `json:"packetType"`
	PacketName string `json:"packetTypeName"`
}

func boundedRead(r io.Reader, limit int) ([]byte, error) {
	b, err := io.ReadAll(io.LimitReader(r, int64(limit)+1))
	if err != nil {
		return nil, err
	}
	if len(b) > limit {
		return nil, fmt.Errorf("decompressed payload exceeds %d bytes", limit)
	}
	return b, nil
}
func decompress(raw []byte, limit int) ([]byte, error) {
	var decoder payloadDecoder
	return decoder.decode(raw, limit)
}

// A replay has tens of thousands of independent Brotli streams. Reset reuses
// the codec's tables and input buffer, but each returned output is separately
// owned: callbacks may safely retain decoded records after the next Reset.
type payloadDecoder struct {
	input  bytes.Reader
	reader *brotli.Reader
}

func (d *payloadDecoder) decode(raw []byte, limit int) ([]byte, error) {
	d.input.Reset(raw)
	if d.reader == nil {
		d.reader = brotli.NewReader(&d.input)
	} else if err := d.reader.Reset(&d.input); err != nil {
		return nil, err
	}
	return boundedRead(d.reader, limit)
}
func readHeader(f *os.File) (Header, error) {
	b := make([]byte, HeaderSize)
	if _, err := io.ReadFull(f, b); err != nil {
		return Header{}, fmt.Errorf("truncated header: %w", err)
	}
	if !bytes.Equal(b[:16], []byte("EternalReturnV1\x00")) {
		return Header{}, fmt.Errorf("invalid replay signature")
	}
	end := bytes.IndexByte(b[16:32], 0)
	if end < 0 {
		end = 16
	}
	v := string(b[16 : 16+end])
	for _, c := range []byte(v) {
		if c > 127 {
			return Header{}, fmt.Errorf("non-ASCII version")
		}
	}
	return Header{v, Bytes(b)}, nil
}

// Track the logical offset instead of issuing Seek + two unbuffered reads for
// every small record. The buffer belongs to this pass, never to returned data.
type recordReader struct {
	reader *bufio.Reader
	offset int64
	limit  int
}

func newRecordReader(f io.Reader, limit int) *recordReader {
	return &recordReader{bufio.NewReaderSize(f, 128<<10), HeaderSize, limit}
}

func (stream *recordReader) next(id int) (Record, error) {
	offset := stream.offset
	var header [16]byte
	n, err := io.ReadFull(stream.reader, header[:])
	if err == io.EOF && n == 0 {
		return Record{}, io.EOF
	}
	if err != nil {
		return Record{}, fmt.Errorf("record header at %d: %w", offset, err)
	}
	size := binary.LittleEndian.Uint32(header[8:12])
	if uint64(size) > uint64(stream.limit) {
		return Record{}, fmt.Errorf("record at %d exceeds payload limit", offset)
	}
	r := Record{ID: id, Offset: offset, Kind: binary.LittleEndian.Uint16(header[:2]), Version: binary.LittleEndian.Uint16(header[2:4]), Tick: binary.LittleEndian.Uint32(header[4:8]), Length: size, Aux: binary.LittleEndian.Uint32(header[12:]), Packets: []Packet{}}
	r.Payload = make(Bytes, int(size))
	if _, err = io.ReadFull(stream.reader, r.Payload); err != nil {
		return Record{}, fmt.Errorf("record payload at %d: %w", offset, err)
	}
	stream.offset += int64(16) + int64(size)
	return r, nil
}
func definitions(f *os.File, limit int) (map[string]string, map[int32]string, error) {
	var defs []definition
	blocks := 0
	stream := newRecordReader(f, limit)
	for id := 1; ; id++ {
		r, err := stream.next(id)
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil, nil, err
		}
		if r.Kind != 3 || r.Version != 2 {
			continue
		}
		blocks++
		z, err := gzip.NewReader(bytes.NewReader(r.Payload))
		if err != nil {
			return nil, nil, err
		}
		raw, err := boundedRead(z, limit)
		closeErr := z.Close()
		if err != nil {
			return nil, nil, err
		}
		if closeErr != nil {
			return nil, nil, closeErr
		}
		var body struct {
			Definitions []definition `json:"definitions"`
		}
		if err = json.Unmarshal(raw, &body); err != nil {
			return nil, nil, err
		}
		if body.Definitions == nil {
			return nil, nil, fmt.Errorf("missing definitions list")
		}
		defs = body.Definitions
	}
	if blocks != 1 {
		return nil, nil, fmt.Errorf("expected one definitions block, found %d", blocks)
	}
	bases := map[string]string{}
	names := map[int32]string{}
	for _, d := range defs {
		if d.Name != "" {
			bases[d.Name] = d.Base
		}
		n := d.PacketName
		if n == "" {
			n = d.Name
		}
		if d.PacketType > 0 && n != "" {
			if old, ok := names[d.PacketType]; ok && old != n {
				return nil, nil, fmt.Errorf("conflicting packet type %d", d.PacketType)
			}
			names[d.PacketType] = n
		}
	}
	return bases, names, nil
}
func parseEnvelope(raw []byte, tick uint32) (packets []Packet, err error) {
	defer func() {
		if r := recover(); r != nil {
			if e, ok := r.(decodeError); ok {
				packets = nil
				err = e
			} else {
				panic(r)
			}
		}
	}()
	c := &cursor{data: raw}
	if c.u8() != 5 {
		fail("ReplayPacketList header mismatch")
	}
	if uint32(c.i32()) != tick {
		fail("delta tick mismatch")
	}
	packets = []Packet{}
	wrappers := func(category string, header byte, itemBox *int32) {
		count := c.count(100000)
		minimum := 9 // header, packet type and byte-array length
		switch category {
		case "clientPackets":
			minimum += 8
		case "commands", "ignoreOrderPackets":
			minimum += 4
		}
		if count > (len(raw)-c.offset)/minimum {
			fail("wrapper collection exceeds payload")
		}
		if count > 0 {
			packets = slices.Grow(packets, count)
		}
		ordinal := 0
		if category == "itemBoxPackets" {
			for i := len(packets) - 1; i >= 0 && packets[i].Category == category; i-- {
				ordinal++
			}
		}
		for i := 0; i < count; i++ {
			if c.u8() != header {
				fail("packet wrapper header mismatch")
			}
			ptype := c.i32()
			length := c.count(len(raw))
			var payload Bytes
			if length >= 0 {
				payload = Bytes(c.take(length))
			}
			p := Packet{Ordinal: ordinal + i, Category: category, PacketType: &ptype, ItemBoxID: itemBox, Raw: payload}
			switch category {
			case "clientPackets":
				u := int64(binary.LittleEndian.Uint64(c.take(8)))
				p.UserID = &u
			case "ignoreOrderPackets", "commands":
				t := binary.LittleEndian.Uint32(c.take(4))
				p.Target = &t
			}
			packets = append(packets, p)
		}
	}
	wrappers("ignoreOrderPackets", 3, nil)
	wrappers("commands", 3, nil)
	count := c.count(100000)
	for i := 0; i < count; i++ {
		id := c.i32()
		wrappers("itemBoxPackets", 2, &id)
	}
	wrappers("clientPackets", 3, nil)
	if c.offset != len(raw) {
		fail("delta consumed %d of %d bytes", c.offset, len(raw))
	}
	return packets, nil
}

// DecodeFile reads every record and attempts every command and full snapshot.
// Unknown/failed payloads remain in the output. A callback consumes one record
// at a time, so decoding does not retain an entire match's object graph.
func DecodeFile(path string, options Options, onHeader func(Header) error, onRecord func(Record) error) (summary Summary, err error) {
	started := time.Now()
	summary = Summary{Format: "er-go-decode.v1", Started: started.Format(time.RFC3339Nano), StatusCounts: map[string]int{}, TypeCounts: map[string]int{}, PhaseSeconds: map[string]float64{}}
	defer func() {
		summary.Finished = time.Now().Format(time.RFC3339Nano)
		summary.Seconds = time.Since(started).Seconds()
		if err != nil {
			summary.FatalError = err.Error()
			summary.Complete = false
		}
	}()
	limit := options.MaxPayload
	if limit == 0 {
		limit = DefaultMaxPayload
	}
	if limit < 1 {
		return summary, fmt.Errorf("payload limit must be positive")
	}
	f, e := os.Open(path)
	if e != nil {
		return summary, e
	}
	defer f.Close()
	h, e := readHeader(f)
	if e != nil {
		return summary, e
	}
	summary.Version = h.Version
	schema, e := LoadSchema(options.SchemaDirectory, h.Version)
	if e != nil {
		return summary, e
	}
	phase := time.Now()
	bases, names, e := definitions(f, limit)
	if e != nil {
		return summary, e
	}
	summary.PhaseSeconds["definitions"] = time.Since(phase).Seconds()
	if _, e = f.Seek(0, io.SeekStart); e != nil {
		return summary, e
	}
	hasher := sha256.New()
	if _, e = io.Copy(hasher, f); e != nil {
		return summary, e
	}
	summary.SourceSHA256 = hex.EncodeToString(hasher.Sum(nil))
	if _, e = f.Seek(HeaderSize, io.SeekStart); e != nil {
		return summary, e
	}
	if onHeader != nil {
		if e = onHeader(h); e != nil {
			return summary, e
		}
	}
	decoder := NewDecoder(schema, h.Version, bases)
	var payloads payloadDecoder
	stream := newRecordReader(f, limit)
	summary.Complete = true
	for id := 1; ; id++ {
		phase = time.Now()
		r, e := stream.next(id)
		summary.PhaseSeconds["recordRead"] += time.Since(phase).Seconds()
		if e == io.EOF {
			break
		}
		if e != nil {
			return summary, e
		}
		summary.RecordCount++
		r.Status = "source-preserved"
		if (r.Kind == 1 || r.Kind == 2) && r.Version == 1 {
			phase = time.Now()
			raw, decompressErr := payloads.decode(r.Payload, limit)
			summary.PhaseSeconds["brotli"] += time.Since(phase).Seconds()
			if decompressErr != nil {
				r.Status = "envelope-failed"
				r.Error = decompressErr.Error()
			} else if r.Kind == 1 {
				phase = time.Now()
				r.Packets, e = parseEnvelope(raw, r.Tick)
				summary.PhaseSeconds["envelope"] += time.Since(phase).Seconds()
				if e != nil {
					r.Status = "envelope-failed"
					r.Error = e.Error()
				} else {
					r.Status = "envelope-decoded"
				}
			} else {
				r.Status = "snapshot-preserved"
				r.Packets = []Packet{{Ordinal: 0, Category: "fullSnapshot", Name: "ReplaySnapshot", Raw: Bytes(raw)}}
			}
		} else if r.Kind == 3 && r.Version == 2 {
			r.Status = "definitions-preserved"
		}
		if r.Status == "envelope-failed" {
			summary.StatusCounts[r.Status]++
			summary.Complete = false
		}
		phase = time.Now()
		for i := range r.Packets {
			p := &r.Packets[i]
			if p.PacketType != nil {
				p.Name = names[*p.PacketType]
			}
			switch {
			case p.Name == "":
				p.Status = "unknown-packet-type"
			case !decoder.Supports(p.Name):
				p.Status = "unknown-schema-type"
			case p.Raw == nil:
				p.Status = "decode-failed"
				p.Error = "null packet payload"
			default:
				value, decodeErr := decoder.DecodeExact(p.Raw, p.Name)
				if decodeErr != nil {
					p.Status = "decode-failed"
					p.Error = decodeErr.Error()
				} else {
					p.Status = "decoded"
					p.Value = value
				}
			}
			summary.PacketCount++
			summary.StatusCounts[p.Status]++
			name := p.Name
			if name == "" {
				name = "<unknown>"
			}
			summary.TypeCounts[name]++
			if p.Status != "decoded" {
				summary.Complete = false
			}
		}
		summary.PhaseSeconds["memorypack"] += time.Since(phase).Seconds()
		if !options.KeepRawRecords && r.Status != "envelope-failed" && r.Status != "source-preserved" {
			r.Payload = nil
		}
		if onRecord != nil {
			phase = time.Now()
			if e = onRecord(r); e != nil {
				return summary, e
			}
			summary.PhaseSeconds["output"] += time.Since(phase).Seconds()
		}
	}
	return summary, nil
}
