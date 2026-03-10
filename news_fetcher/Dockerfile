#Dockefile new_fetcher
FROM python:3.10-slim

WORKDIR /app

# install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# copy project code
COPY aggregator ./aggregator
COPY news_fetcher ./news_fetcher
#COPY app.py .

CMD ["python"]
