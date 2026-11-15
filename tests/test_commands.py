from app.conversation.commands import parse_command


def test_parse_command():
    assert parse_command("#profile kaiser") == ("profile", "kaiser")
    assert parse_command("profile Kaiser") == ("profile", "kaiser")
    assert parse_command("who am i") == ("whoami", "")
    assert parse_command("#who am i") == ("whoami", "")
    assert parse_command("follow up") == ("followup", "")
    assert parse_command("#lang es") == ("lang", "es")
    assert parse_command("language es") == ("lang", "es")
    assert parse_command("reset all") == ("reset", "all")
    assert parse_command("#profile aetna") == ("profile", "aetna")  # hashed: unknown label reply


def test_parse_command_rejects_questions():
    assert parse_command("is there a language barrier") is None
    assert parse_command("language barrier at the clinic") is None
    assert parse_command("profile of a good urgent care?") is None
    assert parse_command("reset my shoulder?") is None
    assert parse_command("think i sprained my ankle") is None
