package replay

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
)

// Member is immutable once a Decoder has compiled its layout.
type Member struct {
	Order                 int
	Type, Name, Declaring string
}
type class struct {
	Base    string
	Members []json.RawMessage
}
type Schema struct {
	Classes map[string]class
	Enums   map[string]int
	Unions  map[string]map[string]string
}

func LoadSchema(directory, version string) (*Schema, error) {
	name := "schema.json"
	switch version {
	case "12.1.0", "12.2.0", "12.3.0":
	case "12.4.0":
		name = "schema-12.4.json"
	default:
		return nil, fmt.Errorf("unreviewed replay version %q", version)
	}
	data, err := os.ReadFile(filepath.Join(directory, name))
	if err != nil {
		return nil, err
	}
	if version == "12.4.0" {
		sum := sha256.Sum256(data)
		if hex.EncodeToString(sum[:]) != "7772e36dbeb0d0a51ad3cd147bf24dc9a91eac34c5492623e4274b1f9b182584" {
			return nil, fmt.Errorf("exact 12.4 schema hash mismatch")
		}
	}
	var s Schema
	if err = json.Unmarshal(data, &s); err != nil {
		return nil, err
	}
	if len(s.Classes) == 0 || len(s.Enums) == 0 {
		return nil, fmt.Errorf("empty schema")
	}
	if version != "12.4.0" {
		s.Unions = nil
	}
	return &s, nil
}

func (d *Decoder) members(name string) ([]Member, error) {
	if m, ok := d.layouts[name]; ok {
		return m, nil
	}
	seen := map[string]bool{}
	var chain []string
	for current := name; current != ""; {
		if seen[current] {
			return nil, fmt.Errorf("inheritance cycle at %s", current)
		}
		seen[current] = true
		chain = append(chain, current)
		base := d.bases[current]
		if base == "" {
			base = d.schema.Classes[current].Base
		}
		current = base
	}
	byOrder := map[int]Member{}
	for i := len(chain) - 1; i >= 0; i-- {
		declaring := chain[i]
		cl := d.schema.Classes[declaring]
		var declared []Member
		for _, raw := range cl.Members {
			var a []json.RawMessage
			if err := json.Unmarshal(raw, &a); err != nil || len(a) != 3 {
				return nil, fmt.Errorf("invalid member in %s", declaring)
			}
			m := Member{Declaring: declaring}
			if json.Unmarshal(a[0], &m.Order) != nil || json.Unmarshal(a[1], &m.Type) != nil || json.Unmarshal(a[2], &m.Name) != nil {
				return nil, fmt.Errorf("invalid member in %s", declaring)
			}
			declared = append(declared, m)
		}
		shift := 0
		if declaring == name && len(declared) > 0 && len(byOrder) > 0 && d.bases[name] != "" && d.bases[name] != cl.Base {
			overlap := false
			minOrder := declared[0].Order
			maxBase := -1
			for _, m := range declared {
				if _, ok := byOrder[m.Order]; ok {
					overlap = true
				}
				minOrder = min(minOrder, m.Order)
			}
			for order := range byOrder {
				maxBase = max(maxBase, order)
			}
			if overlap {
				shift = maxBase + 1 - minOrder
			}
		}
		for _, m := range declared {
			m.Order += shift
			if old, ok := byOrder[m.Order]; ok && old != m {
				return nil, fmt.Errorf("conflicting member order in %s", name)
			}
			byOrder[m.Order] = m
		}
	}
	result := make([]Member, 0, len(byOrder))
	for _, m := range byOrder {
		result = append(result, m)
	}
	sort.Slice(result, func(i, j int) bool { return result[i].Order < result[j].Order })
	d.layouts[name] = result
	return result, nil
}

func (d *Decoder) wireMembers(name string) ([]Member, error) {
	if m, ok := d.wire[name]; ok {
		return m, nil
	}
	m, err := d.members(name)
	if err != nil {
		return nil, err
	}
	m = append([]Member(nil), m...)
	if d.version == "12.3.0" {
		switch name {
		case "CmdPlaySkillActionBase", "CmdPlaySkillAction", "CmdPlaySkillActionWithTargets", "CmdPlayStateSkillAction":
			base, e := d.members("CmdPlaySkillActionBase")
			if e != nil {
				return nil, e
			}
			want := []string{"objectId", "skillId", "casterId", "actionNo"}
			if len(base) != len(want) {
				return nil, fmt.Errorf("12.3 skill-action baseline mismatch")
			}
			for i, n := range want {
				if base[i].Name != n {
					return nil, fmt.Errorf("12.3 skill-action baseline mismatch")
				}
			}
			m = []Member{base[0], base[1], {2, "int", "actionNo", "CmdPlaySkillActionBase"}}
			if name == "CmdPlayStateSkillAction" {
				m = append(m, Member{3, "int", "casterId", name}, Member{4, "int", "stateGroup", name}, Member{5, "List<SkillActionTarget>", "targets", name})
			}
			if name == "CmdPlaySkillActionWithTargets" {
				m = append(m, Member{3, "List<SkillActionTarget>", "targets", name})
			}
		case "StateSkillScriptSnapshot", "CmdStartStateSkill", "CmdFinishStateSkill":
			count := map[string]int{"StateSkillScriptSnapshot": 6, "CmdStartStateSkill": 5, "CmdFinishStateSkill": 4}[name]
			if len(m) != count {
				return nil, fmt.Errorf("12.3 appended stateGroup baseline mismatch")
			}
			for i, v := range m {
				if v.Order != i {
					return nil, fmt.Errorf("12.3 member order mismatch")
				}
			}
			m = append(m, Member{count, "int", "stateGroup", name})
		}
	}
	d.wire[name] = m
	return m, nil
}
