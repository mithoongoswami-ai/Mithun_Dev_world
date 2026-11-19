
import os
import asyncio
import logging

from dotenv import load_dotenv

load_dotenv(override=True)

import google.auth
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from agent_package.agent import root_agent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

PROJECT_ID = os.getenv("GCP_PROJECT_ID")
LOCATION = os.getenv("BIGQUERY_DATA_AGENT_LOCATION", "eu")
AGENT_ID = os.getenv("BIGQUERY_DATA_AGENT_ID")

RESOURCE_NAME = (
    f"projects/{PROJECT_ID}/locations/{LOCATION}/dataAgents/{AGENT_ID}"
)

APP_NAME = "inventory_diagnostic"
USER_ID = "diagnostic_user"
SESSION_ID = "diagnostic_session"


async def main():

    print("\n========== DATA AGENT DIAGNOSTIC ==========")
    print("Project:", PROJECT_ID)
    print("Data Agent:", RESOURCE_NAME)
    print("ADK Agent:", root_agent.name)

    credentials, adc_project = google.auth.default()

    print("ADC Project:", adc_project)
    print("Credential type:", type(credentials).__name__)

    session_service = InMemorySessionService()

    await session_service.create_session(
        app_name=APP_NAME,
        user_id=USER_ID,
        session_id=SESSION_ID
    )

    runner = Runner(
        agent=root_agent,
        app_name=APP_NAME,
        session_service=session_service
    )

    query = f"""
    Use the Data Agent tool to query this exact resource:

    {RESOURCE_NAME}

    Ask it to count the total number of records in the
    SAP inventory table bronze_raw.raw_inventory.

    Return the actual result from the Data Agent.
    Do not invent or estimate any data.
    """

    message = types.Content(
        role="user",
        parts=[types.Part(text=query)]
    )

    async def consume_events():

        print("\nStarting ADK execution...\n")

        async for event in runner.run_async(
            user_id=USER_ID,
            session_id=SESSION_ID,
            new_message=message
        ):

            print("\n-----------------------------------")
            print("Event author:", event.author)
            print("Final response:", event.is_final_response())

            if getattr(event, "error_code", None):
                print("ERROR CODE:", event.error_code)

            if getattr(event, "error_message", None):
                print("ERROR MESSAGE:", event.error_message)

            if event.content and event.content.parts:

                for part in event.content.parts:

                    if part.function_call:
                        print(
                            "TOOL CALL:",
                            part.function_call.name
                        )
                        print(
                            "TOOL ARGUMENTS:",
                            part.function_call.args
                        )

                    if part.function_response:
                        print(
                            "TOOL RESPONSE:",
                            part.function_response.name
                        )
                        print(
                            "RESPONSE DATA:",
                            str(part.function_response.response)[:5000]
                        )

                    if part.text:
                        print("TEXT:", part.text[:5000])

        print("\n========== DIAGNOSTIC FINISHED ==========")

    try:
        await asyncio.wait_for(
            consume_events(),
            timeout=180
        )

    except asyncio.TimeoutError:
        print("\nTIMEOUT: ADK did not finish within 180 seconds.")

    except Exception as exc:
        print("\nADK EXECUTION FAILED:")
        logging.exception(exc)


if __name__ == "__main__":
    asyncio.run(main())
