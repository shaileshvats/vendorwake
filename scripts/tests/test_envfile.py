import os
import stat

import envfile

SAMPLE = """# header comment
VW_ENV=local

# --- Auth0
AUTH0_CLIENT_ID=
AUTH0_CLIENT_SECRET='quoted value'
RENDER_API_KEY=rnd_x
"""


def test_parse_handles_comments_quotes_and_blanks():
    env = envfile.parse(SAMPLE)
    assert env == {
        "VW_ENV": "local",
        "AUTH0_CLIENT_ID": "",
        "AUTH0_CLIENT_SECRET": "quoted value",
        "RENDER_API_KEY": "rnd_x",
    }


def test_update_preserves_other_lines_and_appends(tmp_path):
    p = tmp_path / ".env"
    p.write_text(SAMPLE)
    os.chmod(p, 0o644)
    changed = envfile.write_updates(p, {"AUTH0_CLIENT_ID": "abc", "NEW_KEY": '{"a":{"b":"c"}}'}, header="added")
    assert changed == ["AUTH0_CLIENT_ID", "NEW_KEY"]
    text = p.read_text()
    assert "# header comment\nVW_ENV=local\n\n# --- Auth0\nAUTH0_CLIENT_ID=abc\n" in text
    assert text.endswith('# added\nNEW_KEY={"a":{"b":"c"}}\n')
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    assert envfile.load(p)["NEW_KEY"] == '{"a":{"b":"c"}}'
    # second write with same values is a no-op
    assert envfile.write_updates(p, {"AUTH0_CLIENT_ID": "abc"}) == []


def test_mask_hides_secrets():
    assert envfile.mask("AUTH0_CLIENT_SECRET", "s3cr3t") == "<secret, 6 chars>"
    assert envfile.mask("VW_DATABASE_URL", "postgresql://u:p@h/db").startswith("<secret")
    assert envfile.mask("VW_AUTH0_AGENT_CREDENTIALS", "{}").startswith("<secret")
    assert envfile.mask("VW_ENV", "production") == "production"


def test_is_set_treats_placeholders_as_unset():
    env = {"A": "change-me-app", "B": "your-tenant.us.auth0.com", "C": "real"}
    assert not envfile.is_set(env, "A") and not envfile.is_set(env, "B") and envfile.is_set(env, "C")
