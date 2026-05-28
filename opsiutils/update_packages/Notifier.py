# -*- coding: utf-8 -*-

# Copyright (c) uib GmbH <info@uib.de>
# License: AGPL-3.0
"""
E-Mail-Notifications for installed packages.
"""

import email.utils
import smtplib
import time

from opsi.logging import SECRET_REPLACEMENT_STRING, get_logger, secret_filter
from opsi.opsi.service.model.type import to_int, to_string, to_string_list

__all__ = ("DummyNotifier", "EmailNotifier")

logger = get_logger("opsi.general")


class BaseNotifier:
	def __init__(self) -> None:
		self.message = ""

	def appendLine(self, line: str, pre: str = "") -> None:
		"""
		Add another line to the message.

		:param line: Text to add
		:type line: str
		:param pre: Prefix that will be added before the timestampt and text.
		:type pre: str
		"""
		now = time.strftime("%b %d %H:%M:%S", time.localtime())
		filtered_line = line
		for _secret in secret_filter.secrets:
			filtered_line = filtered_line.replace(_secret, SECRET_REPLACEMENT_STRING)

		self.message += f"{pre}{now} {filtered_line}\n"

	def hasMessage(self) -> bool:
		"""
		Check if the notifier already collected a message.

		:rtype: bool
		"""
		return bool(self.message)

	def notify(self) -> None:
		raise NotImplementedError("Has to be implemented by subclass")

	def setSubject(self, new_subject: str) -> None:
		raise NotImplementedError("Has to be implemented by subclass")


class DummyNotifier(BaseNotifier):
	"""
	Notifier that does nothing on `notify()`.
	"""

	def notify(self) -> None:
		pass  # Doing nothing

	def setSubject(self, new_subject: str) -> None:
		pass  # Doing nothing


class EmailNotifier(BaseNotifier):
	"""
	Notify by sending an email.
	"""

	def __init__(
		self,
		smtphost: str = "localhost",
		smtpport: int = 25,
		subject: str = "opsi product updater",
		sender: str = "",
		receivers: list[str] | None = None,
	) -> None:
		super().__init__()

		self.receivers = to_string_list(receivers or [])
		if not self.receivers:
			raise ValueError("List of mail recipients empty")
		self.smtphost = to_string(smtphost)
		self.smtpport = to_int(smtpport)
		self.sender = to_string(sender)
		self.subject = to_string(subject)
		self.username: str | None = None
		self.password: str | None = None
		self.useStarttls = False

	def setSubject(self, new_subject: str) -> None:
		logger.info("Setting new subject %s", new_subject)
		self.subject = to_string(new_subject)

	def notify(self) -> None:
		logger.notice("Sending mail notification")
		mail = f"From: {self.sender}\n"
		mail += f"To: {','.join(self.receivers)}\n"
		mail += f"Date: {email.utils.formatdate(localtime=True)}\n"
		mail += f"Subject: {self.subject}\n"
		mail += "\n"
		# mail += _("opsi product updater carried out the following actions:") + "\n"
		mail += self.message
		smtpObj = None
		try:
			smtpObj = smtplib.SMTP(self.smtphost, self.smtpport)
			smtpObj.ehlo_or_helo_if_needed()

			if self.useStarttls:
				if smtpObj.has_extn("STARTTLS"):
					logger.debug("Enabling STARTTLS")
					smtpObj.starttls()
				else:
					logger.debug("Server does not support STARTTLS.")

			if self.username and self.password is not None:
				logger.debug('Trying to authenticate against SMTP server %s:%s as user "%s"', self.smtphost, self.smtpport, self.username)
				smtpObj.login(self.username, self.password)
				smtpObj.ehlo_or_helo_if_needed()

			smtpObj.sendmail(self.sender, self.receivers, mail)
			logger.debug("SMTP-Host: '%s' SMTP-Port: '%s'", self.smtphost, self.smtpport)
			logger.debug("Sender: '%s' Reveivers: '%s' Message: '%s'", self.sender, self.receivers, mail)
			logger.notice("Email successfully sent")
			smtpObj.quit()
		except Exception as err:
			if smtpObj is not None:
				logger.debug("SMTP Server does esmtp: %s", smtpObj.does_esmtp)
				if hasattr(smtpObj, "ehlo_resp"):
					logger.debug("SMTP EHLO response: %s", smtpObj.ehlo_resp)

				if hasattr(smtpObj, "esmtp_features"):
					logger.debug("ESMTP Features: %s", smtpObj.esmtp_features)

			raise RuntimeError(f"Failed to send email using smtp server '{self.smtphost}': {err}") from err
