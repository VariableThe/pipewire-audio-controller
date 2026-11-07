import pytest
from monitor_layout.controller import LayoutController
from monitor_layout.model import DeviceMapping, LayoutError, Layout
from monitor_layout.profiles import layout_from_dict, layout_to_dict
from monitor_layout.routing import generate_pipewire_conf
from monitor_layout.audiodev import AudioSink


def setup_layout():
    c = LayoutController(Layout())
    m = c.add_monitor()
    c.sinks = [AudioSink(node_id=1, name='dev.a', description='Fixture', channels=['FR', 'FL'], kind='speaker')]
    c.map_device(m.id, 'dev.a')
    return c, m


def test_selection_does_not_confirm_physical_count():
    c, m = setup_layout()
    assert c.mapping_for(m.id).confirmed_speakers == 0
    c.confirm_channel_order(m.id, ['FR', 'FL'])
    assert c.mapping_for(m.id).confirmed_speakers == 0
    assert c.export_conf()[2]
    c.confirm_speaker_count(m.id, 2)
    assert not c.export_conf()[2]


@pytest.mark.parametrize('labels', [['FL'], ['FL', 'FL'], ['FL', 'AUX9'], ['FL', 'bad label']])
def test_invalid_order_is_atomic(labels):
    c, m = setup_layout()
    c.confirm_channel_order(m.id, ['FR', 'FL'])
    with pytest.raises(LayoutError):
        c.confirm_channel_order(m.id, labels)
    assert c.mapping_for(m.id).channel_order == ['FR', 'FL']


def test_order_persists_and_legacy_load_requires_confirmation():
    c, m = setup_layout()
    c.confirm_channel_order(m.id, ['FR', 'FL'])
    data = layout_to_dict(c.layout)
    assert layout_from_dict(data).mappings[m.id].channel_order == ['FR', 'FL']
    data['schema'] = 1
    assert layout_from_dict(data).mappings[m.id].channel_order == []


def test_count_and_device_changes_clear_order():
    c, m = setup_layout()
    c.confirm_channel_order(m.id, ['FR', 'FL'])
    c.set_speaker_count(m.id, 3)
    assert c.mapping_for(m.id).channel_order == []
    c.set_speaker_count(m.id, 2)
    c.confirm_channel_order(m.id, ['FR', 'FL'])
    c.sinks.append(AudioSink(2, 'dev.b', 'Second', ['FL', 'FR'], kind='speaker'))
    c.map_device(m.id, 'dev.b')
    assert c.mapping_for(m.id).channel_order == []


def test_export_requires_explicit_order_and_current_labels():
    c, m = setup_layout()
    c.confirm_speaker_count(m.id, 2)
    assert c.export_conf()[2]
    c.confirm_channel_order(m.id, ['FR', 'FL'])
    c.sinks[0].channels = ['AUX0', 'AUX1']
    assert c.export_conf()[2]
    with pytest.raises(LayoutError):
        generate_pipewire_conf(c.layout)


@pytest.mark.parametrize('edge', ['top', 'bottom', 'left', 'right'])
def test_slot_identity_survives_geometry_main_and_list_changes(edge):
    c, m = setup_layout()
    c.set_speaker_edge(m.id, edge)
    c.confirm_speaker_count(m.id, 2)
    c.confirm_channel_order(m.id, ['FR', 'FL'])
    second = c.add_monitor(x=100)
    for action in (lambda: c.move_monitor(m.id, 200, 50),
                   lambda: c.rotate_monitor(m.id),
                   lambda: c.set_main(second.id),
                   lambda: c.reorder(m.id, 1)):
        action()
        speakers = sorted((s for s in c.layout.speakers() if s.monitor_id == m.id), key=lambda s: s.slot)
        assert [s.key for s in speakers] == [f'{m.id}/L', f'{m.id}/R']
        points = [(s.x, s.y) for s in speakers]
        assert points == m.speaker_anchors()
        text, _, errors = c.export_conf()
        assert not errors
        assert f'{m.id}/L -> FR, {m.id}/R -> FL' in text
