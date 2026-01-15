import os


class Config:
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
    ARANGO_URL = os.getenv("ARANGO_URL", "http://localhost:8529")
    ARANGO_DB = os.getenv("ARANGO_DB", "financial_kg")
    ARANGO_USERNAME = os.getenv("ARANGO_USERNAME", "root")
    ARANGO_PASSWORD = os.getenv("ARANGO_PASSWORD", "")
    QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
    COHERE_API_KEY = os.getenv("COHERE_API_KEY", "")
    GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")

    # OpenCode configuration
    OPENCODE_CONFIG_PATH = os.getenv("OPENCODE_CONFIG_PATH", "/opencode-config")
    MCP_SERVERS_PATH = os.getenv("MCP_SERVERS_PATH", "/mcp-servers")
    OPENCODE_AGENT = os.getenv("OPENCODE_AGENT", "")


config = Config()
