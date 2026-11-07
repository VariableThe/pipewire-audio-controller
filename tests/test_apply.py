from monitor_layout.apply import install_conf, rollback_config, RESTART_HINT


def test_repeated_install_preserves_unique_backups(tmp_path, monkeypatch):
    monkeypatch.setattr("monitor_layout.apply.time.time_ns", lambda: 42)
    a = "context.modules = [] # first\n"
    b = "context.modules = [] # second\n"
    c = "context.modules = [] # third\n"
    install_conf(a, tmp_path)
    one = install_conf(b, tmp_path)
    two = install_conf(c, tmp_path)
    assert one.backup != two.backup
    assert one.backup.read_text() == a
    assert two.backup.read_text() == b
    assert two.target.read_text() == c
    assert not list(tmp_path.glob("*.tmp"))


def test_rollback_restores_without_audio_commands(tmp_path):
    install_conf("context.modules = [] # original\n", tmp_path)
    install_conf("context.modules = [] # changed\n", tmp_path)
    source = rollback_config(tmp_path)
    assert (tmp_path / "monitor-layout.conf").read_text() == source.read_text()
    assert "wpctl" not in RESTART_HINT
    assert "systemctl" not in RESTART_HINT
