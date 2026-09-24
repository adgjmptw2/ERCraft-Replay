package replay

import (
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"math"
	"strconv"
	"strings"
	"unicode/utf16"
	"unicode/utf8"
)

// Object retains a shared field layout and contiguous values, rather than a map
// per decoded object. Maps are constructed only at the JSON output boundary.
type Object struct {
	Type    string
	Members []Member
	Values  []any
}

func (v Object) MarshalJSON() ([]byte, error) {
	m := make(map[string]any, len(v.Values)+1)
	m["__type"] = v.Type
	for i, x := range v.Values {
		m[v.Members[i].Name] = x
	}
	return json.Marshal(m)
}

type Bytes []byte

func (v Bytes) MarshalJSON() ([]byte, error) {
	if v == nil {
		return []byte("null"), nil
	}
	return json.Marshal(struct {
		Data string `json:"__bytesBase64__"`
	}{base64.StdEncoding.EncodeToString(v)})
}

type number float64

func (v number) MarshalJSON() ([]byte, error) {
	f := float64(v)
	if !math.IsNaN(f) && !math.IsInf(f, 0) {
		return json.Marshal(f)
	}
	s := "NaN"
	if math.IsInf(f, 1) {
		s = "Infinity"
	}
	if math.IsInf(f, -1) {
		s = "-Infinity"
	}
	return json.Marshal(map[string]string{"__float__": s})
}

type decodeError struct{ message string }

func (e decodeError) Error() string { return e.message }
func fail(format string, a ...any)  { panic(decodeError{fmt.Sprintf(format, a...)}) }

type cursor struct {
	data   []byte
	offset int
}

func (c *cursor) take(n int) []byte {
	if n < 0 || n > len(c.data)-c.offset {
		fail("payload boundary at %d: need %d, length %d", c.offset, n, len(c.data))
	}
	b := c.data[c.offset : c.offset+n]
	c.offset += n
	return b
}
func (c *cursor) u8() byte   { return c.take(1)[0] }
func (c *cursor) i32() int32 { return int32(binary.LittleEndian.Uint32(c.take(4))) }
func (c *cursor) count(maximum int) int {
	n := int(c.i32())
	if n < -1 || n > maximum {
		fail("invalid collection count %d", n)
	}
	return n
}

type plan struct {
	kind, name, element, key string
	width                    int
	fields                   []fieldPlan
	err                      error
}
type fieldPlan struct {
	member Member
	plan   *plan
}

// Decoder caches immutable type layouts for one replay/version. Do not share a
// Decoder between goroutines; use one per worker if parallel decoding is needed.
type Decoder struct {
	schema        *Schema
	version       string
	bases         map[string]string
	layouts, wire map[string][]Member
	plans         map[string]*plan
}

func NewDecoder(s *Schema, version string, bases map[string]string) *Decoder {
	b := make(map[string]string, len(bases))
	for k, v := range bases {
		b[k] = v
	}
	return &Decoder{s, version, b, map[string][]Member{}, map[string][]Member{}, map[string]*plan{}}
}
func (d *Decoder) Supports(name string) bool {
	_, ok := d.schema.Classes[name]
	return ok || (d.version == "12.3.0" && name == "CmdPlayStateSkillAction" && d.bases[name] != "")
}

var primitives = map[string]int{"bool": 1, "byte": 1, "sbyte": 1, "short": 2, "ushort": 2, "char": 2, "int": 4, "uint": 4, "long": 8, "ulong": 8, "float": 4, "double": 8}
var vectors = map[string]int{"Vector2Int": 2, "Vector2": 2, "Vector3": 3, "Vector3Int": 3, "Quaternion": 4, "Color": 4, "Color32": 4}

func (d *Decoder) enumSize(name string) int {
	if n := d.schema.Enums[name]; n != 0 {
		return n
	}
	if i := strings.LastIndexByte(name, '.'); i >= 0 {
		return d.schema.Enums[name[i+1:]]
	}
	return 0
}
func genericPair(s string) (string, string) {
	depth := 0
	for i, c := range s {
		switch c {
		case '<':
			depth++
		case '>':
			depth--
		case ',':
			if depth == 0 {
				return s[:i], s[i+1:]
			}
		}
	}
	fail("invalid generic pair %s", s)
	return "", ""
}
func (d *Decoder) getPlan(name string) *plan {
	if p := d.plans[name]; p != nil {
		return p
	}
	p := &plan{name: name, kind: "unknown"}
	d.plans[name] = p
	if _, ok := d.schema.Unions[name]; ok {
		p.kind = "union"
		return p
	}
	if n := primitives[name]; n != 0 {
		p.kind = "primitive"
		p.width = n
		return p
	}
	switch {
	case name == "byte[]":
		p.kind = "bytes"
	case strings.HasSuffix(name, "[]"):
		p.kind = "list"
		p.element = strings.TrimSuffix(name, "[]")
	case (strings.HasPrefix(name, "List<") || strings.HasPrefix(name, "HashSet<")) && strings.HasSuffix(name, ">"):
		p.kind = "list"
		p.element = name[strings.IndexByte(name, '<')+1 : len(name)-1]
	case strings.HasPrefix(name, "Dictionary<") && strings.HasSuffix(name, ">"):
		p.kind = "dictionary"
		p.key, p.element = genericPair(name[11 : len(name)-1])
	case strings.HasPrefix(name, "Nullable<") && strings.HasSuffix(name, ">"):
		p.kind = "nullable"
		p.element = name[9 : len(name)-1]
	case name == "string":
		p.kind = "string"
	case d.enumSize(name) != 0:
		p.kind = "enum"
		p.width = d.enumSize(name)
	case vectors[name] != 0:
		p.kind = "vector"
		p.width = vectors[name]
	case name == "SnapshotWrapper":
		p.kind = "wrapper"
	case d.Supports(name):
		p.kind = "object"
		members, err := d.wireMembers(name)
		p.err = err
		for _, m := range members {
			p.fields = append(p.fields, fieldPlan{member: m})
		}
	}
	return p
}
func uintValue(b []byte) uint64 {
	switch len(b) {
	case 1:
		return uint64(b[0])
	case 2:
		return uint64(binary.LittleEndian.Uint16(b))
	case 4:
		return uint64(binary.LittleEndian.Uint32(b))
	case 8:
		return binary.LittleEndian.Uint64(b)
	}
	fail("unsupported enum width %d", len(b))
	return 0
}
func primitive(c *cursor, name string, width int) any {
	b := c.take(width)
	switch name {
	case "bool":
		return b[0] != 0
	case "sbyte":
		return int8(b[0])
	case "short":
		return int16(binary.LittleEndian.Uint16(b))
	case "int":
		return int32(binary.LittleEndian.Uint32(b))
	case "long":
		return int64(binary.LittleEndian.Uint64(b))
	case "float":
		return number(math.Float32frombits(binary.LittleEndian.Uint32(b)))
	case "double":
		return number(math.Float64frombits(binary.LittleEndian.Uint64(b)))
	default:
		return uintValue(b)
	}
}
func (d *Decoder) layout(name string) (int, int) {
	if n := primitives[name]; n != 0 {
		return n, min(n, 8)
	}
	if n := d.enumSize(name); n != 0 {
		return n, min(n, 8)
	}
	if n := vectors[name]; n != 0 {
		if name == "Color32" {
			return n, 1
		}
		return n * 4, 4
	}
	fail("unsupported unmanaged nullable type %s", name)
	return 0, 0
}
func align(n, a int) int { return (n + a - 1) / a * a }
func readString(c *cursor) any {
	n := c.i32()
	if n == -1 {
		return nil
	}
	if n == 0 {
		return ""
	}
	if n < 0 {
		byteLength := int(^n)
		utf16Length := int(c.i32())
		b := c.take(byteLength)
		if !utf8.Valid(b) {
			fail("invalid UTF-8")
		}
		units := 0
		for _, r := range string(b) {
			units++
			if r > 0xffff {
				units++
			}
		}
		if units != utf16Length {
			fail("UTF-16 length mismatch")
		}
		return string(b)
	}
	if int64(n)*2 > int64(len(c.data)-c.offset) {
		fail("UTF-16 boundary")
	}
	b := c.take(int(n) * 2)
	runes := make([]rune, 0, int(n))
	for i := 0; i < len(b); i += 2 {
		u := binary.LittleEndian.Uint16(b[i:])
		if u >= 0xd800 && u <= 0xdbff {
			if i+3 >= len(b) {
				fail("unpaired UTF-16 high surrogate")
			}
			v := binary.LittleEndian.Uint16(b[i+2:])
			if v < 0xdc00 || v > 0xdfff {
				fail("invalid UTF-16 pair")
			}
			runes = append(runes, utf16.DecodeRune(rune(u), rune(v)))
			i += 2
		} else {
			if u >= 0xdc00 && u <= 0xdfff {
				fail("unpaired UTF-16 low surrogate")
			}
			runes = append(runes, rune(u))
		}
	}
	return string(runes)
}
func (d *Decoder) read(c *cursor, p *plan, depth int) any {
	if depth > 128 {
		fail("maximum nesting depth exceeded")
	}
	if p.err != nil {
		fail("%s", p.err)
	}
	switch p.kind {
	case "primitive":
		return primitive(c, p.name, p.width)
	case "enum":
		return uintValue(c.take(p.width))
	case "vector":
		if p.name == "Color32" {
			b := c.take(4)
			return [4]uint8{b[0], b[1], b[2], b[3]}
		}
		// Fixed-size values need one boxed array, not a slice plus an
		// individually boxed number for every coordinate.
		b := c.take(p.width * 4)
		if strings.HasSuffix(p.name, "Int") {
			var v [3]int32
			for i := 0; i < p.width; i++ {
				v[i] = int32(binary.LittleEndian.Uint32(b[i*4:]))
			}
			if p.width == 2 {
				return [2]int32{v[0], v[1]}
			}
			return v
		}
		var v [4]number
		for i := 0; i < p.width; i++ {
			v[i] = number(math.Float32frombits(binary.LittleEndian.Uint32(b[i*4:])))
		}
		switch p.width {
		case 2:
			return [2]number{v[0], v[1]}
		case 3:
			return [3]number{v[0], v[1], v[2]}
		default:
			return v
		}
	case "bytes":
		n := c.count(len(c.data))
		if n < 0 {
			return nil
		}
		return Bytes(c.take(n))
	case "string":
		return readString(c)
	case "nullable":
		size, a := d.layout(p.element)
		off := align(1, a)
		raw := c.take(align(off+size, a))
		if raw[0] > 1 {
			fail("invalid nullable hasValue")
		}
		if raw[0] == 0 {
			return nil
		}
		nested := &cursor{data: raw[off : off+size]}
		v := d.read(nested, d.getPlan(p.element), depth+1)
		if nested.offset != size {
			fail("nullable value length mismatch")
		}
		return v
	case "list", "dictionary":
		n := c.count(100000)
		if n < 0 {
			return nil
		}
		if n > len(c.data)-c.offset {
			fail("collection exceeds payload")
		}
		v := make([]any, n)
		ep := d.getPlan(p.element)
		if p.kind == "list" {
			for i := range v {
				v[i] = d.read(c, ep, depth+1)
			}
			return v
		}
		if d.version == "12.4.0" && p.key == "long" && p.element == "bool" {
			for i := range v {
				b := c.take(16)
				if b[8] > 1 {
					fail("invalid unmanaged dictionary bool")
				}
				v[i] = []any{int64(binary.LittleEndian.Uint64(b)), b[8] != 0}
			}
			return v
		}
		kp := d.getPlan(p.key)
		for i := range v {
			v[i] = []any{d.read(c, kp, depth+1), d.read(c, ep, depth+1)}
		}
		return v
	case "union":
		tag := c.u8()
		if tag == 255 {
			return nil
		}
		if tag >= 250 {
			fail("unsupported union encoding")
		}
		concrete := d.schema.Unions[p.name][strconv.Itoa(int(tag))]
		if _, ok := d.schema.Classes[concrete]; !ok || concrete == p.name {
			fail("unknown union tag %d for %s", tag, p.name)
		}
		return d.object(c, d.getPlan(concrete), depth+1)
	case "wrapper":
		return d.wrapper(c, depth)
	case "object":
		return d.object(c, p, depth)
	default:
		fail("unknown type %s at %d", p.name, c.offset)
	}
	return nil
}
func (d *Decoder) object(c *cursor, p *plan, depth int) any {
	if depth > 128 {
		fail("maximum object nesting exceeded")
	}
	count := int(c.u8())
	if count == 255 {
		return nil
	}
	if p.err != nil {
		fail("%s", p.err)
	}
	// Union types are concrete objects when used as an explicitly named base.
	if p.kind != "object" {
		members, err := d.wireMembers(p.name)
		if err != nil {
			fail("%s", err)
		}
		if len(p.fields) == 0 {
			for _, m := range members {
				p.fields = append(p.fields, fieldPlan{member: m})
			}
		}
	}
	if count > len(p.fields) {
		fail("%s header %d exceeds %d members", p.name, count, len(p.fields))
	}
	members, err := d.wireMembers(p.name)
	if err != nil {
		fail("%s", err)
	}
	values := make([]any, count)
	for i := range values {
		f := &p.fields[i]
		if f.plan == nil {
			f.plan = d.getPlan(f.member.Type)
		}
		values[i] = d.read(c, f.plan, depth+1)
	}
	return Object{p.name, members[:count], values}
}
func (d *Decoder) wrapper(c *cursor, depth int) any {
	if c.offset >= len(c.data) {
		fail("missing snapshot wrapper")
	}
	header := c.data[c.offset]
	if d.version == "12.4.0" {
		want := []Member{{0, "ObjectType", "objectType", ""}, {1, "int", "objectId", ""}, {2, "InWorldType", "inWorldType", ""}, {3, "byte[]", "snapshot", ""}, {4, "Vector2", "positionXZ", ""}, {5, "int", "positionY", ""}, {6, "uint", "blisLiteRotation", ""}}
		members, e := d.members("SnapshotWrapper")
		if e != nil || len(members) != len(want) {
			fail("12.4 wrapper schema mismatch")
		}
		for i, m := range members {
			if m.Order != want[i].Order || m.Type != want[i].Type || m.Name != want[i].Name {
				fail("12.4 wrapper schema mismatch")
			}
		}
		if d.schema.Enums["InWorldType"] != 4 || d.schema.Enums["ObjectType"] != 4 || header != 7 && header != 255 {
			fail("12.4 wrapper header/schema mismatch")
		}
		return d.object(c, d.getPlan("SnapshotWrapper"), depth)
	}
	if header == 255 {
		c.offset++
		return nil
	}
	if header == 0 || header == 1 {
		c.offset++
		name := "SnapshotWrapperBasic"
		if header == 1 {
			name = "SnapshotWrapperFull"
		}
		return d.object(c, d.getPlan(name), depth)
	}
	if header == 4 {
		return d.object(c, d.getPlan("SnapshotWrapperBasic"), depth)
	}
	if header != 7 {
		fail("unsupported snapshot wrapper header %d", header)
	}
	c.offset++
	name := "SnapshotWrapperFull"
	members, err := d.members(name)
	if err != nil {
		fail("%s", err)
	}
	byName := map[string]Member{}
	for _, m := range members {
		byName[m.Name] = m
	}
	order := []string{"objectType", "objectId", "positionXZ", "positionY", "blisLiteRotation", "inWorldType", "snapshot"}
	if d.version == "12.3.0" {
		order = []string{"objectType", "objectId", "inWorldType", "snapshot", "positionXZ", "positionY", "blisLiteRotation"}
	}
	values := make([]any, len(order))
	wire := make([]Member, len(order))
	for i, n := range order {
		m, ok := byName[n]
		if !ok {
			fail("missing wrapper member %s", n)
		}
		wire[i] = m
		switch {
		case n == "inWorldType" && d.version == "12.3.0":
			values[i] = c.i32()
		case n == "positionY" && d.version != "12.3.0":
			if c.u8() != 1 {
				fail("legacy positionY header mismatch")
			}
			values[i] = c.i32()
		default:
			values[i] = d.read(c, d.getPlan(m.Type), depth+1)
		}
	}
	return Object{name, wire, values}
}

// DecodeExact never silently skips bytes. A failed payload produces no value.
func (d *Decoder) DecodeExact(payload []byte, name string) (value any, err error) {
	defer func() {
		if r := recover(); r != nil {
			if e, ok := r.(decodeError); ok {
				value = nil
				err = e
			} else {
				panic(r)
			}
		}
	}()
	if !d.Supports(name) {
		return nil, fmt.Errorf("unknown schema type %s", name)
	}
	c := &cursor{data: payload}
	p := d.getPlan(name)
	if p.kind == "union" {
		value = d.read(c, p, 0)
	} else {
		value = d.object(c, p, 0)
	}
	if c.offset != len(payload) {
		fail("%s consumed %d of %d bytes", name, c.offset, len(payload))
	}
	return value, nil
}

// DecodeValue exposes typed fields (including unions and SnapshotWrapper) for
// callers decoding nested opaque blobs with a known, externally proven type.
func (d *Decoder) DecodeValue(payload []byte, name string) (value any, err error) {
	defer func() {
		if r := recover(); r != nil {
			if e, ok := r.(decodeError); ok {
				value = nil
				err = e
			} else {
				panic(r)
			}
		}
	}()
	c := &cursor{data: payload}
	value = d.read(c, d.getPlan(name), 0)
	if c.offset != len(payload) {
		fail("%s consumed %d of %d bytes", name, c.offset, len(payload))
	}
	return value, nil
}
