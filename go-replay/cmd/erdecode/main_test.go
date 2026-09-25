package main

import (
	"bytes"
	"compress/gzip"
	"encoding/binary"
	"flag"
	"os"
	"path/filepath"
	"testing"

	"github.com/andybalholm/brotli"
)

func fixture(t *testing.T, gap bool) string {
	t.Helper()
	header := make([]byte, 0x410)
	copy(header, []byte("EternalReturnV1\x0012.4.0\x00"))
	var z bytes.Buffer
	w := gzip.NewWriter(&z)
	_, _ = w.Write([]byte(`{"definitions":[]}`))
	if e := w.Close(); e != nil {
		t.Fatal(e)
	}
	appendRecord := func(kind, version uint16, payload []byte) {
		b := make([]byte, 16)
		binary.LittleEndian.PutUint16(b, kind)
		binary.LittleEndian.PutUint16(b[2:], version)
		binary.LittleEndian.PutUint32(b[8:], uint32(len(payload)))
		header = append(header, b...)
		header = append(header, payload...)
	}
	appendRecord(3, 2, z.Bytes())
	if gap {
		// One unknown command in a tick-zero envelope.
		b := []byte{5}
		i32 := func(v uint32) { p := make([]byte, 4); binary.LittleEndian.PutUint32(p, v); b = append(b, p...) }
		i32(0)
		i32(0)
		i32(1)
		b = append(b, 3)
		i32(123)
		i32(1)
		b = append(b, 0)
		i32(0)
		i32(0)
		i32(0)
		z.Reset()
		br := brotli.NewWriterLevel(&z, 1)
		_, _ = br.Write(b)
		if e := br.Close(); e != nil {
			t.Fatal(e)
		}
		appendRecord(1, 1, z.Bytes())
	}
	p := filepath.Join(t.TempDir(), "fixture.er")
	if e := os.WriteFile(p, header, 0600); e != nil {
		t.Fatal(e)
	}
	return p
}
func invoke(t *testing.T, args ...string) int {
	t.Helper()
	oldArgs, oldFlags := os.Args, flag.CommandLine
	defer func() { os.Args = oldArgs; flag.CommandLine = oldFlags }()
	os.Args = append([]string{"erdecode"}, args...)
	flag.CommandLine = flag.NewFlagSet("erdecode", flag.ContinueOnError)
	return run()
}
func TestExitCodesAndOutputPublication(t *testing.T) {
	schemas := "../../../core/schema"
	input := fixture(t, false)
	output := filepath.Join(t.TempDir(), "result.ndjson")
	if code := invoke(t, "-input", input, "-schema-dir", schemas, "-out", output); code != 0 {
		t.Fatal(code)
	}
	original, e := os.ReadFile(output)
	if e != nil || !bytes.Contains(original, []byte(`"complete":true`)) {
		t.Fatal("missing complete output", e)
	}
	if code := invoke(t, "-input", input, "-schema-dir", schemas, "-out", output); code != 1 {
		t.Fatal("existing output overwritten")
	}
	after, _ := os.ReadFile(output)
	if !bytes.Equal(original, after) {
		t.Fatal("existing output changed")
	}
	gap := fixture(t, true)
	gapOutput := filepath.Join(t.TempDir(), "gap.ndjson")
	if code := invoke(t, "-input", gap, "-schema-dir", schemas, "-out", gapOutput); code != 2 {
		t.Fatal("gap must exit 2", code)
	}
	b, e := os.ReadFile(gapOutput)
	if e != nil || !bytes.Contains(b, []byte(`"complete":false`)) {
		t.Fatal("gap output missing", e)
	}
	if code := invoke(t, "-input", gap, "-schema-dir", schemas, "-allow-gaps"); code != 0 {
		t.Fatal(code)
	}
	if code := invoke(t, "-input", input+".missing", "-schema-dir", schemas); code != 1 {
		t.Fatal(code)
	}
	if code := invoke(t); code != 1 {
		t.Fatal("missing input accepted")
	}
}
