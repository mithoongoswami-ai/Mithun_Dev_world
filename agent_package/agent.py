
import os
import google.auth

from dotenv import load_dotenv

from google.adk.agents import Agent
from google.adk.tools import google_search

from google.adk.tools.data_agent import (
    DataAgentToolset,
    DataAgentCredentialsConfig
)

from google.adk.tools.data_agent.config import (
    DataAgentToolConfig
)

# Load environment variables
load_dotenv()

# BigQuery Data Agent configuration
GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID")

BIGQUERY_DATA_AGENT_ID = os.getenv(
    "BIGQUERY_DATA_AGENT_ID"
)

BIGQUERY_DATA_AGENT_LOCATION = os.getenv(
    "BIGQUERY_DATA_AGENT_LOCATION", "eu"
)

if not GCP_PROJECT_ID or not BIGQUERY_DATA_AGENT_ID:
    raise ValueError(
        "Missing GCP_PROJECT_ID or BIGQUERY_DATA_AGENT_ID in .env"
    )

# Construct the full Data Agent resource name
BIGQUERY_DATA_AGENT_RESOURCE = (
    f"projects/{GCP_PROJECT_ID}"
    f"/locations/{BIGQUERY_DATA_AGENT_LOCATION}"
    f"/dataAgents/{BIGQUERY_DATA_AGENT_ID}"
)

# Load Google Application Default Credentials
credentials, _ = google.auth.default(
    quota_project_id=GCP_PROJECT_ID
)

# Configure authentication for BigQuery Data Agent
credentials_config = DataAgentCredentialsConfig(
    credentials=credentials
)

# Configure the Data Agent toolset
data_agent_config = DataAgentToolConfig(
    location=BIGQUERY_DATA_AGENT_LOCATION,
    max_query_result_rows=50,
    enable_data_agent_modification=False
)

bigquery_toolset = DataAgentToolset(
    credentials_config=credentials_config,
    data_agent_tool_config=data_agent_config
)

# Define the root ADK agent
root_agent = Agent(
    name="agent_package",

    # Retaining your existing model
    model="gemini-3.8-flash",

    description=(
        "A friendly conversational assistant "
        "with access to Google Search and a "
        "governed BigQuery Data Agent."
    ),

    instruction=f"""
    You are a friendly and helpful AI assistant.

    Your responsibilities:

    1. Greet users warmly when they start a conversation.
    2. Introduce yourself as an AI assistant.
    3. Ask users how you can help them.
    4. Answer general questions clearly and politely.
    5. Keep responses simple and easy to understand.
    6. If the user says hello, hi, or hey,
       respond with a friendly greeting.
    7. Maintain a professional and welcoming tone.

    TOOL USAGE:

    You have access to two specialist capabilities.

    A. GOOGLE SEARCH

    Use google_search for questions requiring
    current information from the internet.

    B. BIGQUERY DATA AGENT

    You have access to an existing governed
    BigQuery Data Agent.

    Its full resource name is:

    {BIGQUERY_DATA_AGENT_RESOURCE}

    Whenever users ask questions about:
    - SAP material master data
    - MRP exception messages
    - Purchase orders
    - Scheduling agreements
    - ASN information
    - Inventory data
    - Enterprise data stored in BigQuery

    You MUST use the ask_data_agent tool.

    Pass the full Data Agent resource name above
    and the user's natural-language question.

    Do not generate your own SQL when the
    BigQuery Data Agent can answer the question.

    Do not invent query results.

    Explain the BigQuery Agent's results in
    a clear business-friendly manner.

    If the BigQuery Agent cannot answer,
    communicate the error or limitation honestly.

    Example greeting:

    "Hello! Welcome. I am your AI assistant.
    How can I help you today?"
    """,

    tools=[
        google_search,
        bigquery_toolset
    ],
)
