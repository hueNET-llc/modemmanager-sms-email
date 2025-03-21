import colorlog
import logging
import json
import os
import re
import sys

from datetime import datetime
from email.mime.text import MIMEText
from smtplib import SMTP
from smtplib import SMTPResponseException
from time import sleep
from subprocess import Popen, PIPE

log = logging.getLogger('SMS')

SMS_ID_REGEX = re.compile(r'\/org\/freedesktop\/ModemManager[\d]*\/SMS\/([\d]*)')

class SMS:
    def __init__(self):
        # Setup logging
        self._setup_logging()

        # Modem firmware version, fetched after login
        self.wa_inner_version = ''

        self.blacklist = {
            'numbers': [],
            'words': []
        }

        # Load environment variables
        self._load_env_vars()
        # Load the SMS blacklist
        self._load_blacklist()
        # Last received SMS (duplicate detection)
        self.last_sms = None

    def _setup_logging(self):
        """
            Sets up logging colors and formatting
        """
        # Create a new handler with colors and formatting
        shandler = logging.StreamHandler(stream=sys.stdout)
        shandler.setFormatter(colorlog.LevelFormatter(
            fmt={
                'DEBUG': '{log_color}{asctime} [{levelname}] {message}',
                'INFO': '{log_color}{asctime} [{levelname}] {message}',
                'WARNING': '{log_color}{asctime} [{levelname}] {message}',
                'ERROR': '{log_color}{asctime} [{levelname}] {message}',
                'CRITICAL': '{log_color}{asctime} [{levelname}] {message}',
            },
            log_colors={
                'DEBUG': 'blue',
                'INFO': 'white',
                'WARNING': 'yellow',
                'ERROR': 'red',
                'CRITICAL': 'bg_red',
            },
            style='{',
            datefmt='%Y-%m-%d %H:%M:%S'
        ))
        # Add the new handler
        logging.getLogger('SMS').addHandler(shandler)
        log.debug('Finished setting up logging')

    def _load_env_vars(self):
        """
        Load and process environment variables
        """
        try:
            log_level = os.environ.get('LOG_LEVEL', 'INFO').upper()
            if log_level not in ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'):
                raise ValueError
        except ValueError:
            log.critical('Invalid LOG_LEVEL, must be a valid log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)')
            exit(1)

        # Set the log level
        log.setLevel({'DEBUG': logging.DEBUG, 'INFO': logging.INFO, 'WARNING': logging.WARNING, 'ERROR': logging.ERROR, 'CRITICAL': logging.CRITICAL}[log_level])

        try:
            self.modem_id = int(os.environ['MODEM_ID'])
        except ValueError:
            log.error('Invalid MODEM_IP environment variable, must be a number')
            exit(1)
        except KeyError:
            log.error('Missing MODEM_ID environment variable')
            exit(1)

        try:
            self.poll_interval = int(os.environ.get('POLL_INTERVAL', 30))
            if self.poll_interval < 0:
                raise ValueError
        except ValueError:
            log.error('Invalid POLL_INTERVAL environment variable, must be a number >= 0')
            exit(1)

        try:
            delete_sms = os.environ.get('DELETE_SMS', 'true').lower()
            if delete_sms not in ('true', 'false'):
                raise ValueError
            self.delete_sms = delete_sms == 'true'
        except ValueError:
            log.error('Invalid DELETE_SMS environment variable, must be "true" or "false"')
            exit(1)

        try:
            ignore_existing_sms = os.environ.get('IGNORE_EXISTING_SMS', 'true').lower()
            if ignore_existing_sms not in ('true', 'false'):
                raise ValueError
            self.ignore_existing_sms = ignore_existing_sms == 'true'
        except ValueError:
            log.error('Invalid IGNORE_EXISTING_SMS environment variable, must be "true" or "false"')
            exit(1)

        try:
            self.smtp_host = os.environ['SMTP_HOST']
        except KeyError:
            log.error('Missing SMTP_HOST environment variable')
            exit(1)

        # Get the SMTP port and ensure it's a valid port number
        try:
            self.smtp_port = int(os.environ.get('SMTP_PORT', 25))
        except ValueError:
            log.exception('Invalid SMTP_PORT environment variable, must be a number')
            exit(1)

        # Optional SMTP settings
        # Login is not required
        self.smtp_username = os.environ.get('SMTP_USERNAME')
        self.smtp_password = os.environ.get('SMTP_PASSWORD')

        log.debug(f'Using SMTP login {self.smtp_username} and {self.smtp_password}')

        # Get the SMTP TLS setting and ensure it's a valid boolean
        try:
            self.smtp_tls = bool(os.environ.get('SMTP_TLS', False))
        except ValueError:
            log.exception('Invalid SMTP_TLS environment variable, must be a boolean')
            exit(1)

        try:
            self.smtp_sender = os.environ['SMTP_SENDER']
        except KeyError:
            log.error('Missing SMTP_SENDER environment variable')
            exit(1)

        try:
            self.smtp_recipients = os.environ['SMTP_RECIPIENTS'].split(',')
        except KeyError:
            log.error('Missing SMTP_RECIPIENTS environment variable')
            exit(1)
        if len(self.smtp_recipients) == 0:
            log.error('SMTP_RECIPIENTS environment variable must contain at least one recipient')
            exit(1)

        log.info(f'Loaded {len(self.smtp_recipients)} SMTP recipients: {self.smtp_recipients}')

        self.smtp_subject = os.environ.get('SMTP_SUBJECT', '')

    def _load_blacklist(self):
        """
        Load the SMS word and number blacklist from a JSON file blacklist.json
        """
        try:
            with open('blacklist.json', 'r') as f:
                blacklist = json.load(f)
                for word in blacklist.get('words', []):
                    self.blacklist['words'].append(re.compile(word))
                for number in blacklist.get('numbers', []):
                    self.blacklist['numbers'].append(re.compile(number))
            log.info(f'Loaded blacklist with {len(self.blacklist["words"])} words and {len(self.blacklist["numbers"])} numbers')
        except FileNotFoundError:
            log.info('blacklist.json not found, not using a blacklist')
            self.blacklist = []
        except json.decoder.JSONDecodeError:
            log.warning('blacklist.json does not contain valid JSON')

    def fetch_sms_inbox(self) -> list[int]:
        """
        Fetch the SMS list from the modem using mmcli
        """
        # Run mmcli to get the SMS list
        p = Popen(['mmcli', '--modem', f'{self.modem_id}', '--messaging-list-sms', '--output-json'], stdout=PIPE, stderr=PIPE)
        output, err = p.communicate()
        if p.returncode != 0:
            err = err.decode()
            log.error(f'Failed to fetch SMS list: {err}')
            # Check if the modem ID changed and we have auto-detection enabled
            if "couldn't find modem" in err and int(os.environ['MODEM_ID']) == -1:
                log.warning('Modem no longer exists, re-detecting modem')
                new_modem_id = self.autodetect_modem()
                # Check if we were able to auto-detect the new modem ID
                if new_modem_id is not None:
                    self.modem_id = new_modem_id
            return []
        
        sms_inbox = []
        for sms in json.loads(output)['modem.messaging.sms']:
            sms_inbox.append(self.parse_sms_id(sms))

        return sms_inbox
    
    def fetch_sms_message(self, sms_id: int) -> dict:
        """
        Fetch an SMS message from the modem using mmcli
        """
        # Run mmcli to get the SMS details
        p = Popen(['mmcli', '--modem', f'{self.modem_id}', '--sms', f'{sms_id}', '--output-json'], stdout=PIPE, stderr=PIPE)
        out, err = p.communicate()
        if p.returncode != 0:
            log.error(f'Failed to fetch SMS message {sms_id}: {err.decode()}')
            return {}
        
        message = json.loads(out)['sms']

        return {
            'number': message['content']['number'],
            'content': message['content']['text'],
            'timestamp': message['properties']['timestamp'],
            'state': message['properties']['state']
        }
    
    def delete_sms_message(self, sms_id: int):
        """
        Delete an SMS message from the modem using mmcli
        """
        # Message fails to delete sometimes, retry 2 times on error
        # 'GDBus.Error:org.freedesktop.ModemManager1.Error.Core.Failed: Couldn't delete 1 parts from this SMS'
        for attempt in range(3):
            # Run mmcli to delete the SMS message
            p = Popen(['mmcli', '--modem', f'{self.modem_id}', '--messaging-delete-sms', f'{sms_id}'], stdout=PIPE, stderr=PIPE)
            out, err = p.communicate()
            # Check if the message was deleted successfully
            if p.returncode == 0:
                log.debug(f'Deleted SMS message {sms_id}')
                break
            # Check if the message failed to delete after 3 attempts
            if attempt == 2:
                log.error(f'Failed to delete SMS message {sms_id}: {err.decode()}')

    def parse_sms_id(self, sms_path: str) -> int:
        """
        Parse the SMS ID from a ModemManager dbus path

        Args:
            sms_path (str): SMS dbus path

        Returns:
            int: SMS ID
        """
        # Extract the SMS ID from the dbus path
        match = SMS_ID_REGEX.search(sms_path)
        if match:
            return int(match.group(1))
        else:
            log.error(f'Failed to parse SMS ID from path: {sms_path}')
            return -1
    
    def parse_sms_timestamp(self, timestamp: str) -> datetime:
        """
        Parse the SMS timestamp into a datetime object
        """
        # Remove the timezone offset and parse the timestamp
        return datetime.strptime(f'{timestamp}00', '%Y-%m-%dT%H:%M:%S%z')

    def send_email(self, sender: str, recipient: str | list, subject: str, body: str, smtp_username: str, smtp_password: str, smtp_host: str, smtp_port: int, tls: bool):
        """
        Send an email via SMTP

        Args:
            sender (str): SMTP sender address
            recipient (str | list): Recipient address(es)
            subject (str): Email subject
            body (str): Email body
            smtp_username (str): SMTP username
            smtp_password (str): SMTP password
            smtp_host (str): SMTP host/IP
            smtp_port (int): SMTP port
            tls (bool): Use SMTP TLS
        """
        # Create an SMTP client
        smtp = SMTP(host=smtp_host, port=smtp_port)
        message = MIMEText(body, 'plain', 'utf-8')
        # Set the Subject, Sender, and Recipient
        message['Subject'] = subject
        message['From'] = sender
        message['To'] = ', '.join(recipient)
        if tls:
            # Start TLS session
            smtp.starttls()
        # Login to the SMTP server
        smtp.login(smtp_username, smtp_password)
        # Send the email
        smtp.sendmail(sender, recipient, message.as_string())
        # Close the SMTP session
        smtp.quit()

    def autodetect_modem(self):
        p = Popen(['mmcli', '--list-modems', '--output-json'], stdout=PIPE, stderr=PIPE)
        out, err = p.communicate()
        if p.returncode != 0:
            log.error(f'Failed to auto-detect modem: {err.decode()}')
            return None

        modems = json.loads(out)['modem-list']
        if len(modems) == 0:
            log.error('Failed to auto-detect modem: No modems found')
            return None
        
        modem_id = modems[0]
        log.info(f'Auto-detected modem: {modem_id}')
        return modem_id

    def run(self):
        # Modem auto-detection
        if self.modem_id == -1:
            modem_id = self.autodetect_modem()
            if modem_id is None:
                exit(1)
            self.modem_id = modem_id

        if self.ignore_existing_sms:
            # Login and fetch the initial SMS inbox list on the first run
            while True:
                try:
                    log.info('Fetching initial SMS ID...')
                    # Fetch the initial SMS inbox list
                    initial_sms_list = self.fetch_sms_inbox()
                    if initial_sms_list:
                        # Get the newest SMS ID
                        initial_sms_id = initial_sms_list[0]
                    else:
                        # Fake SMS ID since the list is empty
                        initial_sms_id = -1
                    break
                except Exception:
                    log.warning('Initial SMS ID fetch failed, retrying in 30 seconds')
                    # Wait 30 seconds before retrying
                    sleep(30)

            log.info('Fetched initial SMS ID, waiting for new messages')
        else:
            # Fake initial SMS ID since we care about existing messages
            initial_sms_id = -1

        # Loop forever and check for new SMS messages
        while True:
            # Sleep for the SMS inbox fetch interval
            sleep(self.poll_interval)

            sms_inbox = self.fetch_sms_inbox()
            # Check if the inbox list is empty
            if len(sms_inbox) == 0:
                log.debug('Got empty SMS inbox list, skipping')
                continue

            log.debug(f'Fetched latest SMS inbox list: {sms_inbox}')

            for sms_id in sms_inbox:
                if self.ignore_existing_sms and sms_id <= initial_sms_id:
                    # Skip existing SMS messages
                    continue

                # Fetch the SMS message using its ID (dbus path)
                sms = self.fetch_sms_message(sms_id)

                try:
                    # Get the SMS timestamp
                    timestamp = self.parse_sms_timestamp(sms['timestamp'])
                except ValueError:
                    # This shouldn't happen but who knows
                    log.warning(f'Failed to parse SMS {sms_id} timestamp: {sms["timestamp"]}')
                    # Use the current time as a fallback
                    timestamp = datetime.now()
                    continue

                # Get the SMS content
                content = sms['content']

                blacklist = False
                # Run the SMS content through the blacklist
                for word in self.blacklist['words']:
                    if word.search(content):
                        log.warning(f'Received blacklisted SMS {sms_id} From: {sms["number"]}, Date: {timestamp.ctime()}, Blacklisted Word: {word.pattern}, Message: {content}')
                        blacklist = True
                        break
                # Check if the SMS content is blacklisted
                if blacklist:
                    # Check if we should delete SMS messages
                    if self.delete_sms:
                        # Delete the SMS message
                        self.delete_sms_message(sms_id)
                    continue

                # Run the SMS number through the blacklist
                for number in self.blacklist['numbers']:
                    if number.search(sms['number']):
                        log.warning(f'Received blacklisted SMS {sms_id} From: {sms["number"]}, Date: {timestamp.ctime()}, Blacklisted Number: {number.pattern}, Message: {content}')
                        blacklist = True
                        break
                # Check if the SMS number is blacklisted
                if blacklist:
                    # Check if we should delete SMS messages
                    if self.delete_sms:
                        # Delete the SMS message
                        self.delete_sms_message(sms_id)
                    continue

                # Check if the SMS is a duplicate
                if self.last_sms \
                    and self.last_sms['number'] == sms['number'] \
                    and self.last_sms['content'] == sms['content'] \
                    and self.last_sms['timestamp'] == sms['timestamp']:
                    log.debug(f'Ignoring duplicate SMS {sms_id}')
                    # Check if we should delete SMS messages
                    if self.delete_sms:
                        # Delete the SMS message
                        self.delete_sms_message(sms_id)
                    continue

                log.info(f'Received SMS {sms_id} From: {sms["number"]}, Date: {timestamp.ctime()}, Message: {content}')

                # Keep trying to send until it succeeds in case of network/server issues
                while True:
                    try:
                        self.send_email(
                            sender=self.smtp_sender,
                            recipient=self.smtp_recipients,
                            subject=self.smtp_subject.replace('%number%', sms['number']),
                            body=f'From: {sms["number"]}\nDate: {timestamp.strftime("%a %b %d %H:%M:%S %Y %z")}\nMessage: {content}',
                            smtp_username=self.smtp_username,
                            smtp_password=self.smtp_password,
                            smtp_host=self.smtp_host,
                            smtp_port=self.smtp_port,
                            tls=self.smtp_tls
                        )
                        break
                    except SMTPResponseException as e:
                        log.error(f'Failed to send email, SMTP error: {e}')
                        # Retry SMTP send after 15 seconds
                        sleep(15)

                # Check if we should delete SMS messages
                if self.delete_sms:
                    # Delete the SMS message
                    self.delete_sms_message(sms_id)

                # Store the SMS message for duplicate detection
                self.last_sms = {
                    'number': sms['number'],
                    'content': sms['content'],
                    'timestamp': sms['timestamp']
                }

sms = SMS()
sms.run()
