package replay

import (
	"bytes"
	"compress/gzip"
	"encoding/binary"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"testing"

	"github.com/andybalholm/brotli"
)

func compressed(t *testing.T, raw []byte, gz bool) []byte {
	t.Helper()
	var b bytes.Buffer
	if gz {
		w := gzip.NewWriter(&b)
		if _, e := w.Write(raw); e != nil {
			t.Fatal(e)
		}
		if e := w.Close(); e != nil {
			t.Fatal(e)
		}
	} else {
		w := brotli.NewWriterLevel(&b, 1)
		if _, e := w.Write(raw); e != nil {
			t.Fatal(e)
		}
		if e := w.Close(); e != nil {
			t.Fatal(e)
		}
	}
	return b.Bytes()
}
func framed(kind, version uint16, tick uint32, payload []byte) []byte {
	b := make([]byte, 16, len(payload)+16)
	binary.LittleEndian.PutUint16(b, kind)
	binary.LittleEndian.PutUint16(b[2:], version)
	binary.LittleEndian.PutUint32(b[4:], tick)
	binary.LittleEndian.PutUint32(b[8:], uint32(len(payload)))
	return append(b, payload...)
}
func replayFile(t *testing.T, body ...[]byte) string {
	t.Helper()
	b := make([]byte, HeaderSize)
	copy(b, []byte("EternalReturnV1\x0012.4.0\x00"))
	for _, v := range body {
		b = append(b, v...)
	}
	path := filepath.Join(t.TempDir(), "fixture.er")
	if e := os.WriteFile(path, b, 0600); e != nil {
		t.Fatal(e)
	}
	return path
}
func defRecord(t *testing.T) []byte {
	return framed(3, 2, 0, compressed(t, []byte(`{"definitions":[{"name":"CmdStopMove","packetType":1},{"name":"UnknownPacket","packetType":2},{"name":"CmdDamage","packetType":3}]}`), true))
}
func commandEnvelope(packetType int32, payload []byte) []byte {
	b := append([]byte{5}, le32(42, 0, 1)...)
	b = append(b, 3)
	b = append(b, le32(packetType, int32(len(payload)))...)
	b = append(b, payload...)
	return append(b, le32(99, 0, 0)...)
}
func TestFullFileAndFailureStates(t *testing.T) {
	stop := append([]byte{2}, le32(7, 0, 0)...)
	cases := []struct {
		name    string
		typ     int32
		payload []byte
		want    string
	}{{"valid", 1, stop, "decoded"}, {"unknown", 2, stop, "unknown-schema-type"}, {"unnamed", 999, stop, "unknown-packet-type"}, {"truncated", 1, stop[:5], "decode-failed"}}
	for _, tt := range cases {
		t.Run(tt.name, func(t *testing.T) {
			path := replayFile(t, framed(1, 1, 42, compressed(t, commandEnvelope(tt.typ, tt.payload), false)), defRecord(t), framed(2, 1, 43, compressed(t, []byte{0}, false)), framed(7, 1, 44, []byte{1, 2, 3}), framed(0, 0, 0, nil))
			var output []Record
			summary, e := DecodeFile(path, Options{SchemaDirectory: "../../core/schema", KeepRawRecords: true}, nil, func(r Record) error { output = append(output, r); return nil })
			if e != nil {
				t.Fatal(e)
			}
			if summary.RecordCount != 5 || summary.PacketCount != 2 || summary.StatusCounts[tt.want] < 1 {
				t.Fatalf("%+v", summary)
			}
			if summary.Complete != (tt.want == "decoded") {
				t.Fatal("incorrect complete flag")
			}
			if output[0].Packets[0].Status != tt.want || !bytes.Equal(output[0].Packets[0].Raw, tt.payload) {
				t.Fatal("failed/unknown bytes lost")
			}
			if len(output[3].Payload) != 3 {
				t.Fatal("opaque record lost")
			}
			if _, e = json.Marshal(output); e != nil {
				t.Fatal(e)
			}
		})
	}
}
func TestEnvelopeAllCategoriesAndNull(t *testing.T) {
	b := append([]byte{5}, le32(42, 1)...)
	// ignoreOrderPackets: null payload, routing target.
	b = append(b, 3)
	b = append(b, le32(9, -1, 77, 0, 2)...)
	// Two item boxes, each with one packet, must have ordinals 0 and 1.
	for _, id := range []int32{5, 6} {
		b = append(b, le32(id, 1)...)
		b = append(b, 2)
		b = append(b, le32(9, 0)...)
	}
	b = append(b, le32(1)...)
	b = append(b, 3)
	b = append(b, le32(9, 0)...)
	b = append(b, make([]byte, 8)...)
	packets, e := parseEnvelope(b, 42)
	if e != nil {
		t.Fatal(e)
	}
	if len(packets) != 4 || packets[0].Raw != nil || packets[1].Ordinal != 0 || packets[2].Ordinal != 1 || *packets[2].ItemBoxID != 6 || packets[3].UserID == nil {
		t.Fatalf("%+v", packets)
	}
	if _, e = parseEnvelope(b, 43); e == nil {
		t.Fatal("mismatched tick accepted")
	}
	for i := 0; i < len(b); i++ {
		if _, e = parseEnvelope(b[:i], 42); e == nil {
			t.Fatalf("truncated envelope %d accepted", i)
		}
	}
}
func TestFramingCompressionLimitsAndCallbacks(t *testing.T) {
	def := defRecord(t)
	tests := [][][]byte{{def, def}, {framed(0, 0, 0, nil)}, {def, {1, 2, 3}}, {def, framed(1, 1, 42, []byte{255, 0})[:17]}}
	for _, body := range tests {
		path := replayFile(t, body...)
		summary, e := DecodeFile(path, Options{SchemaDirectory: "../../core/schema"}, nil, nil)
		if e == nil || summary.Complete {
			t.Fatal("invalid framing/definitions accepted")
		}
	}
	bad := replayFile(t, def, framed(1, 1, 42, []byte{255, 0}))
	s, e := DecodeFile(bad, Options{SchemaDirectory: "../../core/schema"}, nil, nil)
	if e != nil || s.Complete || s.StatusCounts["envelope-failed"] != 1 {
		t.Fatal("Brotli failure not retained", e, s)
	}
	if _, e = decompress(compressed(t, bytes.Repeat([]byte{0}, 1024), false), 100); e == nil {
		t.Fatal("decompression limit ignored")
	}
	valid := replayFile(t, def)
	marker := errors.New("sink failure")
	s, e = DecodeFile(valid, Options{SchemaDirectory: "../../core/schema"}, nil, func(Record) error { return marker })
	if !errors.Is(e, marker) || s.Complete {
		t.Fatal("sink error lost")
	}
	s, e = DecodeFile(valid, Options{SchemaDirectory: "../../core/schema", MaxPayload: 1}, nil, nil)
	if e == nil || s.Complete {
		t.Fatal("compressed size limit ignored")
	}
}

func TestBrotliReusePreservesOutputAndRecovers(t *testing.T) {
	var decoder payloadDecoder
	first := bytes.Repeat([]byte("first payload"), 200)
	kept, err := decoder.decode(compressed(t, first, false), DefaultMaxPayload)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = decoder.decode([]byte{255, 0}, DefaultMaxPayload); err == nil {
		t.Fatal("invalid stream accepted")
	}
	second := bytes.Repeat([]byte("second"), 1000)
	decoded, err := decoder.decode(compressed(t, second, false), DefaultMaxPayload)
	if err != nil || !bytes.Equal(decoded, second) || !bytes.Equal(kept, first) {
		t.Fatal("Reset lost state/output isolation", err)
	}
	if _, err = decoder.decode(compressed(t, second, false), 3); err == nil {
		t.Fatal("limit ignored")
	}
	decoded, err = decoder.decode(compressed(t, first, false), DefaultMaxPayload)
	if err != nil || !bytes.Equal(decoded, first) {
		t.Fatal("Reset after limit failed", err)
	}
}
