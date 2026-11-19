import uuid
import gradio as gr
from dotenv import load_dotenv
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

# 1. Load .env from the root folder before importing your agent
load_dotenv()
from agent_package.agent import root_agent

APP_NAME = "gradio_adk_app"
USER_ID = "default_user"

# 2. Initialize ADK Session Service and Runner
session_service = InMemorySessionService()
runner = Runner(
    agent=root_agent,
    app_name=APP_NAME,
    session_service=session_service,
)


# 3. Gradio async chat handler
async def chat_with_agent(message: str, history: list, session_id: str):
    existing_session = await session_service.get_session(
        app_name=APP_NAME, user_id=USER_ID, session_id=session_id
    )
    if not existing_session:
        await session_service.create_session(
            app_name=APP_NAME, user_id=USER_ID, session_id=session_id
        )

    user_content = types.Content(
        role="user",
        parts=[types.Part.from_text(text=message)],
    )

    final_response = ""
    async for event in runner.run_async(
        user_id=USER_ID,
        session_id=session_id,
        new_message=user_content,
    ):
        if event.is_final_response() and event.content and event.content.parts:
            final_response = event.content.parts[0].text

    return final_response


# 4. Build and launch the Gradio UI
with gr.Blocks(title="ADK Agent Chat") as demo:
    gr.Markdown("# ADK Agent Web Interface")
    session_state = gr.State(lambda: str(uuid.uuid4()))

    gr.ChatInterface(
        fn=chat_with_agent,
        additional_inputs=[session_state],
      #  type="messages",
    )

if __name__ == "__main__":
    demo.launch()
