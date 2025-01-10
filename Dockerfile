FROM alpine:3.21

COPY . /sms

WORKDIR /sms

RUN apk update && \
    apk add --no-cache python3 py3-pip tzdata modemmanager && \
    pip install --no-cache-dir --break-system-packages -r requirements.txt

ENTRYPOINT ["python", "-u", "sms.py"]