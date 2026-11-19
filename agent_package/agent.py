
import os
import google.auth

from google.adk.agents import Agent
from google.adk.tools.data_agent.data_agent_toolset import DataAgentToolset
from google.adk.tools.data_agent.credentials import DataAgentCredentialsConfig
from google.adk.tools.data_agent.config import DataAgentToolConfig

# ---------------------------------------------------------
# GCP CONFIGURATION
# ---------------------------------------------------------

GCP_PROJECT_ID = os.getenv(
    "GCP_PROJECT_ID",
    "mithun-new-project-491912"
)

DATA_AGENT_ID = os.getenv(
    "DATA_AGENT_ID",
    "agent_dec8fa47-a20b-4270-b5c2-0780976e3532"
)

DATA_AGENT_LOCATION = os.getenv(
    "DATA_AGENT_LOCATION",
    "eu"
)

GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.8-flash"
)

DATA_AGENT_RESOURCE_NAME = (
    f"projects/{GCP_PROJECT_ID}/locations/"
    f"{DATA_AGENT_LOCATION}/dataAgents/{DATA_AGENT_ID}"
)

BQ_TABLE = (
    f"{GCP_PROJECT_ID}.bronze_raw.raw_inventory"
)

# ---------------------------------------------------------
# GOOGLE CLOUD AUTHENTICATION
# ---------------------------------------------------------

credentials, detected_project = google.auth.default(
    scopes=["https://www.googleapis.com/auth/cloud-platform"]
)

print("Google Cloud authentication initialized.")
print("GCP Project:", GCP_PROJECT_ID)
print("Detected project:", detected_project)
print("Data Agent resource:", DATA_AGENT_RESOURCE_NAME)
print("Data Agent location:", DATA_AGENT_LOCATION)
print("Gemini model:", GEMINI_MODEL)

# ---------------------------------------------------------
# DATA AGENT TOOLSET
# ---------------------------------------------------------

credentials, detected_project = google.auth.default(
    scopes=["https://www.googleapis.com/auth/cloud-platform"]
)

data_agent_credentials = DataAgentCredentialsConfig(
    credentials=credentials
)

data_agent_config = DataAgentToolConfig(
    location=DATA_AGENT_LOCATION,
    max_query_result_rows=1000,
    enable_data_agent_modification=False,
)

bigquery_toolset = DataAgentToolset(
    credentials_config=data_agent_credentials,
    data_agent_tool_config=data_agent_config,
    tool_filter=[
        "list_accessible_data_agents",
        "get_data_agent_info",
        "ask_data_agent",
    ],
)

# ---------------------------------------------------------
# AGENT INSTRUCTIONS
# ---------------------------------------------------------

AGENT_INSTRUCTION = f"""
You are an SAP Inventory Intelligence Agent supporting
Material Requirements Planning (MRP) controllers.

Your purpose is to retrieve real inventory data from BigQuery
and answer user questions using the Google Data Agent.

DATA SOURCE
-----------
BigQuery table:
{BQ_TABLE}

Google Data Agent resource:
{DATA_AGENT_RESOURCE_NAME}

Available inventory fields confirmed in the source:
- material_number
- plant
- storage_location
- unrestricted_stock
- safety_stock
- stock_value_eur
- base_uom
- blocked_stock
- in_transit_stock
- quality_inspection_stock
- snapshot_date
- last_count_date
- last_goods_movement_date

The table does not currently have a confirmed material description,
reorder point, or planned procurement start/end dates.

MANDATORY DATA RETRIEVAL
-----------------------
1. For every inventory question, retrieve relevant source data.
2. First call get_data_agent_info using the exact resource name:
   {DATA_AGENT_RESOURCE_NAME}
3. Then call ask_data_agent using that same resource name.
4. Do not rely on previously remembered inventory figures.
5. Apply the user's requested scope, such as plant, material,
   stock category, date, or risk.
6. Request the actual fields needed to answer the question.
7. Return real records and actual values. Never invent records,
   stock quantities, dates, or material descriptions.
8. Do not combine quantities with different base_uom values.
9. Keep unrestricted, blocked, in-transit and quality inspection
   stock separate.
10. If data retrieval fails, set retrieval_status to "failed"
    and explain the error in data_limitations.

ANALYSIS
--------
The application calculates stock risk using the following rules:

Critical:
unrestricted_stock = 0

High:
unrestricted_stock > 0 AND unrestricted_stock < safety_stock

Normal:
unrestricted_stock >= safety_stock

Do not invent a different risk classification.
Do not classify missing stock or safety stock values as Normal.

For user requests involving risk analysis:
- Retrieve the relevant material and plant records.
- Include unrestricted stock and safety stock.
- Include base UOM.
- Include blocked, in-transit and quality stock separately.
- Identify stockout and below-safety-stock situations.
- Explain the findings in practical MRP terms.
- Recommend actions only when supported by the data.

For user requests involving graphical reports:
- Retrieve the actual underlying data.
- Include sufficient records for the requested chart.
- Do not create chart values from assumptions.
- Historical line charts require multiple snapshot dates.
- Gantt charts require actual planned start and end dates.
- If those dates are unavailable, explain why a Gantt chart
  cannot currently be generated.

OUTPUT FORMAT
-------------
Return exactly one valid JSON object, without Markdown fences
or introductory text.

Use this structure:

{{
  "retrieval_status": "success",
  "report_title": "Inventory Intelligence Report",
  "user_query": "Original user request",
  "generated_summary": "Concise explanation of the findings",
  "executive_summary": [
    "Finding one",
    "Finding two"
  ],
  "summary": {{
    "total_materials": null,
    "critical_items": null,
    "high_risk_items": null,
    "normal_items": null,
    "stockout_items": null
  }},
  "inventory": [
    {{
      "material_number": "Actual material number",
      "plant": "Actual plant",
      "storage_location": "Actual storage location",
      "base_uom": "Actual UOM",
      "unrestricted_stock": null,
      "safety_stock": null,
      "stock_value_eur": null,
      "blocked_stock": null,
      "in_transit_stock": null,
      "quality_inspection_stock": null,
      "snapshot_date": null,
      "last_count_date": null,
      "last_goods_movement_date": null
    }}
  ],
  "recommendations": [
    "Actionable recommendation based on retrieved data"
  ],
  "data_limitations": []
}}

IMPORTANT:
- The inventory array must contain actual retrieved records.
- Do not put illustrative or fabricated records in the array.
- Use null for missing source values.
- The application will calculate the final risk categories.
- Do not invent totals. The application calculates totals
  from the returned records.
- If the user requests a filtered report, return records
  matching that request.
- If no records are found, return an empty inventory array
  and explain this in data_limitations.
- If retrieval fails, use retrieval_status = "failed".
"""

# ---------------------------------------------------------
# ROOT AGENT
# ---------------------------------------------------------

root_agent = Agent(
    name="sap_inventory_intelligence_agent",
    model=GEMINI_MODEL,
    description=(
        "SAP inventory intelligence agent for MRP risk analysis, "
        "inventory reporting and material planning."
    ),
    instruction=AGENT_INSTRUCTION,
    tools=[bigquery_toolset],
)

print("SAP Inventory Intelligence Agent initialized successfully.")
