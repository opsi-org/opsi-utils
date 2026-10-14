import smtplib
from email.parser import BytesParser
from email.policy import SMTP
from unittest import mock

import pytest

from opsiutils.update_packages.Notifier import EmailNotifier


@pytest.mark.parametrize("character", ["x", "\u00fc"])
def test_email_notifier_encodes_long_lines(character: str) -> None:
	notifier = EmailNotifier(
		sender="sender@example.org",
		receivers=["first@example.org", "second@example.org"],
		subject="Package update: \u00fc",
	)
	notifier.appendLine(character * 45033)
	notifier.appendLine("Another entry")
	smtp = smtplib.SMTP()
	with (
		mock.patch("opsiutils.update_packages.Notifier.smtplib.SMTP", return_value=smtp),
		mock.patch.object(smtp, "ehlo_or_helo_if_needed"),
		mock.patch.object(smtp, "sendmail") as sendmail,
		mock.patch.object(smtp, "quit") as quit_smtp,
	):
		notifier.notify()

	sendmail.assert_called_once()
	sender, receivers, serialized = sendmail.call_args.args[:3]
	assert sender == notifier.sender
	assert receivers == notifier.receivers
	assert isinstance(serialized, bytes)
	assert max(len(line) for line in serialized.split(b"\r\n")) <= 998
	message = BytesParser(policy=SMTP).parsebytes(serialized)
	assert message["From"] == notifier.sender
	assert message["To"] == ", ".join(notifier.receivers)
	assert message["Subject"] == notifier.subject
	assert message["Content-Transfer-Encoding"] == "quoted-printable"
	assert message.get_content().replace("\r\n", "\n") == notifier.message
	quit_smtp.assert_called_once()
