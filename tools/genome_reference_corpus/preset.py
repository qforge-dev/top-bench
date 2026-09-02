from __future__ import annotations

import base64
import re
import struct
from dataclasses import dataclass
from html import unescape


@dataclass(frozen=True, slots=True)
class ModelPresetState:
    model_path: str
    preset_state_md5: str


def component_xml(data: bytes) -> bytes:
    if len(data) < 48 or data[:4] != b"VST3":
        message = "invalid Genome VST3 preset"
        raise ValueError(message)
    list_offset = struct.unpack_from("<Q", data, 40)[0]
    listing = data[list_offset:]
    if listing[:12] != b"List\x02\x00\x00\x00Comp":
        message = "unsupported Genome VST3 chunk layout"
        raise ValueError(message)
    component_offset = struct.unpack_from("<Q", listing, 12)[0]
    component_size = struct.unpack_from("<Q", listing, 20)[0]
    component = data[component_offset : component_offset + component_size]
    if len(component) < 8 or component[:4] != b"VC2!":
        message = "unsupported Genome component state"
        raise ValueError(message)
    xml_size = struct.unpack_from("<I", component, 4)[0]
    xml_end = 8 + xml_size
    if xml_end > len(component):
        message = "truncated Genome component XML"
        raise ValueError(message)
    return component[8:xml_end]


def session_to_vstpreset(session: bytes, template: bytes) -> bytes:
    """Pack a standalone Genome session into a VST3 preset container."""
    if not re.search(rb"<genome(?:\s[^>]*)?>", session):
        message = "invalid standalone Genome session"
        raise ValueError(message)
    session_xml, open_count = re.subn(
        rb"<genome(?P<attrs>\s[^>]*)?>",
        rb"<Genome\g<attrs>>",
        session,
        count=1,
    )
    session_xml, close_count = re.subn(rb"</genome>", rb"</Genome>", session_xml, count=1)
    if open_count != 1 or close_count != 1:
        message = "invalid standalone Genome session root"
        raise ValueError(message)

    if len(template) < 48 or template[:4] != b"VST3":
        message = "invalid Genome VST3 preset template"
        raise ValueError(message)
    old_list_offset = struct.unpack_from("<Q", template, 40)[0]
    listing = bytearray(template[old_list_offset:])
    if listing[:12] != b"List\x02\x00\x00\x00Comp" or listing[28:32] != b"Cont":
        message = "unsupported Genome VST3 chunk layout"
        raise ValueError(message)
    component_offset = struct.unpack_from("<Q", listing, 12)[0]
    component_size = struct.unpack_from("<Q", listing, 20)[0]
    component = template[component_offset : component_offset + component_size]
    if len(component) < 8 or component[:4] != b"VC2!":
        message = "unsupported Genome component state"
        raise ValueError(message)
    old_xml_size = struct.unpack_from("<I", component, 4)[0]
    old_xml_end = 8 + old_xml_size
    if old_xml_end > len(component):
        message = "truncated Genome component XML"
        raise ValueError(message)
    suffix = component[old_xml_end:]
    new_component = b"VC2!" + struct.pack("<I", len(session_xml)) + session_xml + suffix
    new_list_offset = component_offset + len(new_component)
    header = bytearray(template[:component_offset])
    struct.pack_into("<Q", header, 40, new_list_offset)
    struct.pack_into("<Q", listing, 12, component_offset)
    struct.pack_into("<Q", listing, 20, len(new_component))
    struct.pack_into("<Q", listing, 32, new_list_offset)
    return bytes(header) + new_component + bytes(listing)


def _decoded_snapshots(component_xml: bytes) -> bytes:
    pattern = re.compile(
        rb'<snapshots\b[^>]*obfuscated="1"[^>]*>(.*?)</snapshots>',
        re.DOTALL,
    )
    matches = list(pattern.finditer(component_xml))
    if not matches:
        message = "Genome preset has no encoded snapshots payload"
        raise ValueError(message)
    encrypted = base64.b64decode(b"".join(matches[0].group(1).split()), validate=True)
    prefix = b"<snapshots"
    if len(encrypted) < 4:
        message = "Genome snapshots payload is truncated"
        raise ValueError(message)
    key = bytes(encrypted[index] ^ prefix[index] for index in range(4))
    decoded = bytes(value ^ key[index % 4] for index, value in enumerate(encrypted))
    if not decoded.startswith(prefix):
        message = "could not decode Genome snapshots payload"
        raise ValueError(message)
    return decoded


def _paradex_block(decoded: bytes) -> bytes:
    matches = list(
        re.finditer(
            rb'<parameter\s+name="model-id">\s*<value>([^<]*\.ampnet)</value>',
            decoded,
            re.DOTALL,
        )
    )
    if len(matches) != 1:
        message = "Genome preset has no unique PARADEX AmpNet model"
        raise ValueError(message)
    start = decoded.rfind(b"<effect ", 0, matches[0].start())
    closing = decoded.find(b"</effect>", matches[0].end())
    if start < 0 or closing < 0:
        message = "Genome preset has malformed PARADEX effect state"
        raise ValueError(message)
    return decoded[start : closing + len(b"</effect>")]


def inspect_model_state(data: bytes) -> ModelPresetState:
    block = _paradex_block(_decoded_snapshots(component_xml(data)))
    model_match = re.search(
        rb'<parameter\s+name="model-id">\s*<value>(.*?)</value>', block, re.DOTALL
    )
    md5_match = re.search(rb'<preset-state\s+md5="([^"]*)"', block)
    if model_match is None or md5_match is None:
        message = "Genome preset has no PARADEX model state"
        raise ValueError(message)
    return ModelPresetState(
        model_path=unescape(model_match.group(1).decode()),
        preset_state_md5=md5_match.group(1).decode(),
    )
