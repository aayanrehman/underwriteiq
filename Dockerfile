FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY data/samples ./data/samples
COPY .agents ./.agents
RUN pip install --no-cache-dir ".[llm]"
ENV UNDERWRITEIQ_DB=/tmp/underwriteiq.db
EXPOSE 8000
CMD ["uvicorn", "underwriteiq.api:app", "--host", "0.0.0.0", "--port", "8000"]
