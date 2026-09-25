package replay

import (
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"math"
	"math/big"
	"os"
	"strings"
	"testing"
)

func testDecoder(t *testing.T, version string) *Decoder {
	t.Helper()
	s, e := LoadSchema("../../core/schema", version)
	if e != nil {
		t.Fatal(e)
	}
	return NewDecoder(s, version, map[string]string{"LocalObjectCommandPacket": "ObjectCommandPacket", "CmdPlayStateSkillAction": "CmdPlaySkillActionBase"})
}
func le32(values ...int32) []byte {
	b := make([]byte, len(values)*4)
	for i, v := range values {
		binary.LittleEndian.PutUint32(b[i*4:], uint32(v))
	}
	return b
}
func valueJSON(t *testing.T, v any) string {
	t.Helper()
	b, e := json.Marshal(v)
	if e != nil {
		t.Fatal(e)
	}
	return string(b)
}

func canonicalNumbers(v any) any {
	switch x := v.(type) {
	case json.Number:
		r, ok := new(big.Rat).SetString(string(x))
		if !ok {
			panic("invalid JSON number")
		}
		return "number:" + r.RatString()
	case []any:
		for i := range x {
			x[i] = canonicalNumbers(x[i])
		}
	case map[string]any:
		for k, v := range x {
			x[k] = canonicalNumbers(v)
		}
	}
	return v
}
func TestNullableNativeLayout(t *testing.T) {
	d := testDecoder(t, "12.4.0")
	tests := []struct {
		data []byte
		want string
	}{{append([]byte{1, 0, 0, 0}, le32(-8)...), "-8"}, {make([]byte, 8), "null"}}
	for _, tt := range tests {
		v, e := d.DecodeValue(tt.data, "Nullable<int>")
		if e != nil || valueJSON(t, v) != tt.want {
			t.Fatalf("%v %v", v, e)
		}
	}
	for _, b := range [][]byte{{1}, append([]byte{2, 0, 0, 0}, le32(2)...), append(make([]byte, 8), 0)} {
		if _, e := d.DecodeValue(b, "Nullable<int>"); e == nil {
			t.Fatal("invalid nullable accepted")
		}
	}
}
func TestStringsAndNumbers(t *testing.T) {
	d := testDecoder(t, "12.4.0")
	s := "한😀"
	utf8Bytes := []byte(s)
	b := append(le32(^int32(len(utf8Bytes)), 3), utf8Bytes...)
	v, e := d.DecodeValue(b, "string")
	if e != nil || v != s {
		t.Fatalf("%v %v", v, e)
	}
	b = append(le32(3), []byte{0x5c, 0xd5, 0x3d, 0xd8, 0x00, 0xde}...)
	v, e = d.DecodeValue(b, "string")
	if e != nil || v != s {
		t.Fatalf("%v %v", v, e)
	}
	for _, b := range [][]byte{append(le32(1), 0, 0xd8), append(le32(-3, 2), 0xff, 0xff), append(le32(-3, 9), 'a', 'b')} {
		if _, e = d.DecodeValue(b, "string"); e == nil {
			t.Fatal("invalid string accepted")
		}
	}
	raw := make([]byte, 8)
	binary.LittleEndian.PutUint64(raw, math.MaxUint64)
	v, e = d.DecodeValue(raw, "ulong")
	if e != nil || valueJSON(t, v) != "18446744073709551615" {
		t.Fatal("uint64 precision lost")
	}
	binary.LittleEndian.PutUint64(raw, math.Float64bits(math.Inf(1)))
	v, e = d.DecodeValue(raw, "double")
	if e != nil || valueJSON(t, v) != `{"__float__":"Infinity"}` {
		t.Fatal("nonfinite marker lost")
	}
}
func TestCollectionsAndUnion(t *testing.T) {
	d := testDecoder(t, "12.4.0")
	pair := make([]byte, 16)
	binary.LittleEndian.PutUint64(pair, math.MaxInt64)
	pair[8] = 1
	b := append(le32(1), pair...)
	v, e := d.DecodeValue(b, "Dictionary<long,bool>")
	if e != nil || valueJSON(t, v) != "[[9223372036854775807,true]]" {
		t.Fatalf("%v %v", v, e)
	}
	b[12] = 2
	if _, e = d.DecodeValue(b, "Dictionary<long,bool>"); e == nil {
		t.Fatal("bad boolean accepted")
	}
	for _, b := range [][]byte{le32(-2), le32(100001), le32(4)} {
		if _, e = d.DecodeValue(b, "List<int>"); e == nil {
			t.Fatal("invalid list accepted")
		}
	}
	for _, header := range []byte{250, 254, 199} {
		if _, e = d.DecodeValue([]byte{header}, "MoveCommandPacket"); e == nil {
			t.Fatal("invalid union accepted")
		}
	}
	if v, e = d.DecodeValue([]byte{255}, "MoveCommandPacket"); e != nil || v != nil {
		t.Fatal("null union rejected")
	}
}
func TestVersionOverridesAndIsolation(t *testing.T) {
	d := testDecoder(t, "12.3.0")
	payload := append([]byte{3}, le32(1332, 51, 2)...)
	v, e := d.DecodeExact(payload, "CmdPlaySkillAction")
	if e != nil {
		t.Fatal(e)
	}
	text := valueJSON(t, v)
	if !strings.Contains(text, `"actionNo":2`) || strings.Contains(text, "casterId") {
		t.Fatal(text)
	}
	for _, name := range []string{"CmdStartStateSkill", "CmdFinishStateSkill", "StateSkillScriptSnapshot"} {
		m, e := d.wireMembers(name)
		if e != nil || m[len(m)-1].Name != "stateGroup" {
			t.Fatal(name, e)
		}
	}
	d2 := testDecoder(t, "12.1.0")
	m, e := d2.wireMembers("CmdPlaySkillAction")
	if e != nil {
		t.Fatal(e)
	}
	if len(m) == 3 {
		t.Fatal("version layout leaked")
	}
	for _, version := range []string{"12.1.0", "12.3.0"} {
		d := testDecoder(t, version)
		var raw []byte
		if version == "12.3.0" {
			raw = append([]byte{7}, le32(7, 1234, 2, 3)...)
			raw = append(raw, []byte("abc")...)
			raw = append(raw, le32(int32(math.Float32bits(12.5)), int32(math.Float32bits(-4.25)), 49, 900)...)
		} else {
			raw = append([]byte{7}, le32(7, 1234, int32(math.Float32bits(12.5)), int32(math.Float32bits(-4.25)))...)
			raw = append(raw, 1)
			raw = append(raw, le32(49, 900, 2, 3)...)
			raw = append(raw, []byte("abc")...)
		}
		v, e := d.DecodeValue(raw, "SnapshotWrapper")
		if e != nil || !strings.Contains(valueJSON(t, v), `"positionY":49`) {
			t.Fatal(version, e)
		}
	}
}
func TestStrictBoundariesAndCycles(t *testing.T) {
	d := testDecoder(t, "12.4.0")
	raw := append([]byte{2}, le32(17, 0, 0)...)
	for i := 0; i < len(raw); i++ {
		if _, e := d.DecodeExact(raw[:i], "CmdStopMove"); e == nil {
			t.Fatalf("truncation %d accepted", i)
		}
	}
	if _, e := d.DecodeExact(append(raw, 0), "CmdStopMove"); e == nil {
		t.Fatal("trailing byte accepted")
	}
	cyc := NewDecoder(d.schema, "12.4.0", map[string]string{"CmdStopMove": "CmdStopMove"})
	if _, e := cyc.DecodeExact(raw, "CmdStopMove"); e == nil {
		t.Fatal("cycle accepted")
	}
	if _, e := LoadSchema("../../core/schema", "99.0.0"); e == nil {
		t.Fatal("unreviewed version accepted")
	}
	if valueJSON(t, Bytes(nil)) != "null" || valueJSON(t, Bytes{}) != `{"__bytesBase64__":""}` {
		t.Fatal("null/empty bytes collapsed")
	}
}

// Optional exhaustive differential fixtures are generated by tools/schema_fixtures.py.
func TestPythonFixtures(t *testing.T) {
	path := os.Getenv("ER_GO_FIXTURES")
	if path == "" {
		t.Skip("ER_GO_FIXTURES not set")
	}
	raw, e := os.ReadFile(path)
	if e != nil {
		t.Fatal(e)
	}
	var cases []struct {
		Version, Name, Mode, Hex string
		Bases                    map[string]string
		Valid                    bool
		Expected                 json.RawMessage
	}
	if e = json.Unmarshal(raw, &cases); e != nil {
		t.Fatal(e)
	}
	decoders := map[string]*Decoder{}
	for i, c := range cases {
		key := c.Version + valueJSON(t, c.Bases)
		d := decoders[key]
		if d == nil {
			s, e := LoadSchema("../../core/schema", c.Version)
			if e != nil {
				t.Fatal(e)
			}
			d = NewDecoder(s, c.Version, c.Bases)
			decoders[key] = d
		}
		b, e := hex.DecodeString(c.Hex)
		if e != nil {
			t.Fatal(e)
		}
		var v any
		if c.Mode == "value" {
			v, e = d.DecodeValue(b, c.Name)
		} else {
			v, e = d.DecodeExact(b, c.Name)
		}
		if !c.Valid {
			if e == nil {
				t.Fatalf("case %d %s %s accepted invalid bytes", i, c.Version, c.Name)
			}
			continue
		}
		if e != nil {
			t.Fatalf("case %d %s %s: %v", i, c.Version, c.Name, e)
		}
		var a, bv any
		decoder := json.NewDecoder(strings.NewReader(string(c.Expected)))
		decoder.UseNumber()
		if e = decoder.Decode(&a); e != nil {
			t.Fatal(e)
		}
		decoder = json.NewDecoder(strings.NewReader(valueJSON(t, v)))
		decoder.UseNumber()
		if e = decoder.Decode(&bv); e != nil {
			t.Fatal(e)
		}
		// Re-marshalling preserves integers without float64 precision loss.
		if valueJSON(t, canonicalNumbers(a)) != valueJSON(t, canonicalNumbers(bv)) {
			t.Fatalf("case %d %s %s values differ", i, c.Version, c.Name)
		}
	}
	t.Logf("matched %d Python differential fixtures", len(cases))
}

func FuzzDecodeValue(f *testing.F) {
	s, e := LoadSchema("../../core/schema", "12.4.0")
	if e != nil {
		f.Fatal(e)
	}
	f.Add([]byte{255})
	f.Add([]byte{2, 1, 0, 0, 0})
	f.Fuzz(func(t *testing.T, b []byte) {
		if len(b) > 4096 {
			t.Skip()
		}
		d := NewDecoder(s, "12.4.0", nil)
		for _, n := range []string{"CmdDamage", "ReplaySnapshot", "List<Vector2Int>", "SnapshotWrapper", "string"} {
			_, _ = d.DecodeValue(b, n)
		}
	})
}
