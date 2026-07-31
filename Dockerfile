FROM python:3.14-rc-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir -r req_usertable.txt

COPY . .

ENV DASHBOARD_PORT=8089
ENV DASHBOARD_HOST=0.0.0.0
EXPOSE 8089

CMD ["python", "src/users_table.py"]