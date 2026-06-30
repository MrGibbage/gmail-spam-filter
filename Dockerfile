FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY spam_filter/ ./spam_filter/
HEALTHCHECK --interval=5m --timeout=10s --retries=3 \
  CMD python -c "import json,os,time; s=json.load(open(os.environ.get('STATE_FILE','/data/state.json'))); exit(0 if time.time()-s.get('last_poll',0)<400 else 1)"
CMD ["python", "-m", "spam_filter.main"]
