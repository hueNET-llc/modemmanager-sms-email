# [modemmanager-sms-email](https://github.com/hueNET-llc/modemmanager-sms-email)
[![GitHub Workflow Status](https://img.shields.io/github/actions/workflow/status/huenet-llc/modemmanager-sms-email/master.yml?branch=master)](https://github.com/hueNET-llc/modemmanager-sms-email/actions/workflows/master.yml)
[![Docker Image Version (latest by date)](https://img.shields.io/docker/v/rafaelwastaken/modemmanager-sms-email)](https://hub.docker.com/r/rafaelwastaken/modemmanager-sms-email)

A ModemManager SMS to (SMTP) Email relay

## Requirements
Python requirements are listed in `requirements.txt`.

## blacklist.json ##
Used for blacklisting words/phrases and numbers using case-sensitive regex, useful for blocking annoying carrier advertising

The file should be in the same working directory as `sms.py`

Example:
```
{
    "words": ["meuplano\\.tim\\.com\\.br", "tim\\.com\\.br/primevideo"],
    "numbers": ["TIMInforma", "TIMVantagem"]
}
```

## Environment Variables ##
Configuration is done via environment variables. Any values with "N/A" default are required.

|  Name  | Description | Type | Default | Example |
| ------ | ----------- | ---- | ------- | ------- |
| LOG_LEVEL | Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL) | str | INFO | INFO |
| MODEM_ID | Modem ID. Set to -1 to auto-detect the first availabe modem ID | int | N/A | 0 |
| POLL_INTERVAL | Interval in seconds (>=0) between polling the modem for messages | int | 1 | 5 |
| DELETE_SMS | Delete SMS messages after emailing them | bool | true | false |
| IGNORE_EXISTING_SMS | Ignore existing SMS messages from before script start | bool | true | false |
| SMTP_HOST | SMTP server address | str | N/A | smtp.gmail.com |
| SMTP_PORT | SMTP server port | int | 25 | 587 |
| SMTP_USERNAME | SMTP username | str | Blank | example@gmail.com |
| SMTP_PASSWORD | SMTP password | str | Blank | hunter2 |
| SMTP_TLS | Use SMTP TLS | bool | false | true |
| SMTP_SENDER | SMTP sender address | str | N/A | sms@gmail.com |
| SMTP_RECIPIENTS | SMTP recipient address(es) separated by commas | str | N/A | user1@gmail.com,user2@gmail.com,user3@gmail.com |
| SMTP_SUBJECT | Email subject ("%number%" will be replaced by the sender number) | str | Blank | New SMS from %number% |
