from __future__ import annotations

import struct
import unittest

try:
    from .delta_payloads import Cursor, SchemaDecoder, DecodeError
except ImportError:
    from delta_payloads import Cursor, SchemaDecoder, DecodeError


def direct_full_wrapper(*, version: str) -> bytes:
    if version == "12.3.0":
        return b"".join(
            (
                b"\x07",
                struct.pack("<I", 7),
                struct.pack("<i", 1234),
                struct.pack("<i", 2),
                struct.pack("<i", 3),
                b"abc",
                struct.pack("<ff", 12.5, -4.25),
                struct.pack("<i", 49),
                struct.pack("<I", 900),
            )
        )
    return b"".join(
        (
            b"\x07",
            struct.pack("<I", 7),
            struct.pack("<i", 1234),
            struct.pack("<ff", 12.5, -4.25),
            b"\x01" + struct.pack("<i", 49),
            struct.pack("<I", 900),
            struct.pack("<I", 2),
            struct.pack("<i", 3),
            b"abc",
        )
    )


class SnapshotWrapperFullWireTests(unittest.TestCase):
    def decode(self, data: bytes, version: str | None) -> tuple[dict, SchemaDecoder]:
        decoder = SchemaDecoder({}, client_version=version)
        cursor = Cursor(data)
        value = decoder.read(cursor, "SnapshotWrapper")
        self.assertEqual(cursor.offset, len(data))
        self.assertIsInstance(value, dict)
        return value, decoder

    def test_12_3_direct_full_position_y_is_raw_int(self) -> None:
        value, decoder = self.decode(
            direct_full_wrapper(version="12.3.0"), "12.3.0"
        )
        self.assertEqual(value["positionY"], 49)
        self.assertEqual(value["snapshot"], b"abc")
        self.assertEqual(
            decoder.wire_overrides[
                "SnapshotWrapperFull.inWorldType:12.3-int32"
            ],
            1,
        )

    def test_legacy_direct_full_position_y_keeps_object_header(self) -> None:
        value, decoder = self.decode(
            direct_full_wrapper(version="12.1.0"), "12.1.0"
        )
        self.assertEqual(value["positionY"], 49)
        self.assertEqual(
            decoder.wire_overrides[
                "SnapshotWrapperFull.positionY:legacy-BlisFixedPoint-object"
            ],
            1,
        )

    def test_12_3_user_snapshot_has_no_unverified_synthetic_member(self) -> None:
        decoder = SchemaDecoder({}, client_version="12.3.0")
        names = [member.name for member in decoder.wire_members_of("UserSnapshot")]
        self.assertEqual(
            names[:4],
            [
                "userId",
                "characterSnapshot",
                "playerSnapshot",
                "equips",
            ],
        )
        self.assertEqual(names[-1], "deathRecapOverlayInfoSnapshot")

    def test_12_3_skill_action_with_targets_has_one_exact_target(self) -> None:
        payload = bytes.fromhex(
            "04"
            "34050000"
            "40040000"
            "29000000"
            "01000000"
            "02"
            "00000000"
            "01000000"
            "c4b0f5c2"
            "042933bf"
            "a65cf741"
        )
        decoder = SchemaDecoder(
            {"LocalObjectCommandPacket": "ObjectCommandPacket"},
            client_version="12.3.0",
        )
        decoded = decoder.decode_exact(payload, "CmdPlaySkillActionWithTargets")
        self.assertEqual(decoded["objectId"], 1332)
        self.assertEqual(decoded["skillId"], 1088)
        self.assertEqual(decoded["actionNo"], 41)
        self.assertNotIn("casterId", decoded)
        self.assertEqual(len(decoded["targets"]), 1)
        self.assertEqual(decoded["targets"][0]["targetId"], 0)
        self.assertEqual(len(decoded["targets"][0]["targetPos"]), 3)

    def test_12_3_state_skill_script_appends_exact_int_state_group(self) -> None:
        payload = b"".join(
            (
                b"\x07",
                struct.pack("<I", 3),
                struct.pack("<i", 7102100),
                struct.pack("<i", 0),
                struct.pack("<i", 42),
                struct.pack("<i", 7),
                b"\x00",
                struct.pack("<i", 12345),
            )
        )
        decoder = SchemaDecoder({}, client_version="12.3.0")
        decoded = decoder.decode_exact(payload, "StateSkillScriptSnapshot")
        self.assertEqual(decoded["stateGroup"], 12345)
        self.assertEqual(
            decoder.wire_overrides[
                "StateSkillScriptSnapshot:12.3-appended-int-stateGroup"
            ],
            1,
        )

    def test_12_3_start_state_skill_appends_exact_int_state_group(self) -> None:
        payload = b"".join(
            (
                b"\x06",
                struct.pack("<i", 42),
                struct.pack("<I", 3),
                struct.pack("<i", 7102100),
                struct.pack("<i", 0),
                struct.pack("<i", 7),
                struct.pack("<i", 12345),
            )
        )
        decoder = SchemaDecoder(
            {"LocalObjectCommandPacket": "ObjectCommandPacket"},
            client_version="12.3.0",
        )
        decoded = decoder.decode_exact(payload, "CmdStartStateSkill")
        self.assertEqual(decoded["objectId"], 42)
        self.assertEqual(decoded["stateGroup"], 12345)
        self.assertEqual(
            decoder.wire_overrides[
                "CmdStartStateSkill:12.3-appended-int-stateGroup"
            ],
            1,
        )

    def test_12_3_finish_state_skill_appends_exact_int_state_group(self) -> None:
        payload = b"".join(
            (
                b"\x05",
                struct.pack("<i", 42),
                struct.pack("<I", 3),
                struct.pack("<i", 7),
                struct.pack("<I", 6),
                struct.pack("<i", 12345),
            )
        )
        decoder = SchemaDecoder(
            {"LocalObjectCommandPacket": "ObjectCommandPacket"},
            client_version="12.3.0",
        )
        decoded = decoder.decode_exact(payload, "CmdFinishStateSkill")
        self.assertEqual(decoded["stateGroup"], 12345)
        self.assertEqual(
            decoder.wire_overrides[
                "CmdFinishStateSkill:12.3-appended-int-stateGroup"
            ],
            1,
        )

    def test_12_3_play_state_skill_action_uses_metadata_direct_fields(self) -> None:
        payload = b"".join(
            (
                b"\x06",
                struct.pack("<i", 42),
                struct.pack("<I", 3),
                struct.pack("<i", 9),
                struct.pack("<i", 11),
                struct.pack("<i", 12345),
                struct.pack("<i", 1),
                b"\x02",
                struct.pack("<i", 88),
                b"\x01\x00\x00\x00",
                struct.pack("<fff", 1.0, 2.0, 3.0),
            )
        )
        decoder = SchemaDecoder(
            {
                "LocalObjectCommandPacket": "ObjectCommandPacket",
                "CmdPlayStateSkillAction": "CmdPlaySkillActionBase",
            },
            client_version="12.3.0",
        )
        self.assertTrue(decoder.supports_object_type("CmdPlayStateSkillAction"))
        decoded = decoder.decode_exact(payload, "CmdPlayStateSkillAction")
        self.assertEqual(decoded["casterId"], 11)
        self.assertEqual(decoded["actionNo"], 9)
        self.assertNotIn("stateCasterId", decoded)
        self.assertEqual(decoded["stateGroup"], 12345)
        self.assertEqual(
            decoded["targets"],
            [{"__type": "SkillActionTarget", "targetId": 88, "targetPos": [1.0, 2.0, 3.0]}],
        )
        self.assertEqual(
            decoder.wire_overrides[
                "CmdPlayStateSkillAction:12.3-metadata-direct-state-fields"
            ],
            1,
        )

    def test_12_3_ordinary_action_number_is_not_a_caster(self):
        decoder=SchemaDecoder({"LocalObjectCommandPacket":"ObjectCommandPacket"},client_version="12.3.0")
        row=decoder.decode_exact(b'\x03'+struct.pack('<iIi',1332,51,2),'CmdPlaySkillAction')
        self.assertEqual(row['actionNo'],2)
        self.assertNotIn('casterId',row)

    def test_12_3_target_list_length_is_enforced(self):
        decoder=SchemaDecoder({"LocalObjectCommandPacket":"ObjectCommandPacket"},client_version="12.3.0")
        target=b'\x02'+struct.pack('<i',99)+b'\x00\x00\x00\x00'
        for count in [0,2]:
            with self.assertRaises(DecodeError):
                decoder.decode_exact(b'\x04'+struct.pack('<iIii',1332,51,2,count)+target,'CmdPlaySkillActionWithTargets')

    def test_12_3_empty_target_list_is_valid(self):
        decoder=SchemaDecoder({"LocalObjectCommandPacket":"ObjectCommandPacket"},client_version="12.3.0")
        row=decoder.decode_exact(b'\x04'+struct.pack('<iIii',1332,51,2,0),'CmdPlaySkillActionWithTargets')
        self.assertEqual(row['targets'],[])

if __name__ == "__main__":
    unittest.main()
