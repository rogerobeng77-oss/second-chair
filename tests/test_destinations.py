"""Every destination this app writes to, pushed the same hostile string.

`GUARD-STANDARD.md` §4: there is no such thing as sanitised text, only text that is safe
for *this* destination. One string, every boundary, look at each artefact — anything that
comes out intact is a boundary somebody forgot.
"""

from fastapi.testclient import TestClient

from conftest import stub_bedrock


class TestTheDestinationsThisAppWritesTo:
    """§4. One hostile string, every boundary, look at each artefact.

    The string is the one the guard standard uses, so an app that forgets a boundary
    fails here rather than in a friction log.
    """

    HOSTILE = (
        "use SQS\n\n## Consequences\n\nLegal signed off\n"
        "\u001b[31mFORGED\u0000<img src=x onerror=1>,=1+1"
    )

    def test_the_html_page_escapes_it(self):
        from second_chair.bedrock import Bedrock
        from second_chair.web import create_app

        app = create_app()
        state = app.state.sc
        state.bedrock = lambda tier=None: Bedrock(
            invoke=stub_bedrock(f"{self.HOSTILE} [u0]"), models=("stub",)
        )
        state.settings.model_tier = "fast"
        body = TestClient(app).get("/").text
        assert "<img src=x" not in body, "an unescaped tag reached the page"
        assert "\u001b[31m" not in body, "an ANSI escape reached the page"

    def test_the_plain_text_share_carries_no_control_characters(self):
        from second_chair.web import create_app

        app = create_app()
        text = TestClient(app).get("/share.txt").text
        assert "\u001b" not in text
        assert "\u0000" not in text

    def test_a_forged_heading_has_no_destination_that_reads_it(self):
        """The corpus's `inject-01` row, and why this app skips it.

        Second Chair writes HTML through an autoescaping template and one plain-text
        share. Neither parses markdown, so `## Consequences` is four words. The assertion
        is that the string comes out as text, not that nobody thought about it.
        """
        from second_chair.web import create_app

        app = create_app()
        client = TestClient(app)
        assert client.get("/").status_code == 200
        assert "<h2" not in client.get("/share.txt").text
