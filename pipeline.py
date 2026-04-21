"""
Pipecat Pipeline Assembly
Wires the AI services, tools, and turn-taking logic into a runnable task.
"""

from loguru import logger
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineTask, PipelineParams
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.turns.user_start import VADUserTurnStartStrategy
from pipecat.turns.user_stop import SpeechTimeoutUserTurnStopStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.frames.frames import LLMMessagesAppendFrame, TTSSpeakFrame, EndFrame
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.observers.loggers.metrics_log_observer import MetricsLogObserver

# Import our decoupled components
import tools
from prompts import SYSTEM_PROMPT
from services.stt import get_stt_service
from services.tts import get_tts_service
from services.llm import get_llm_service

def build_pipeline(transport) -> PipelineTask:
    """Assembles the Pipecat pipeline using the provided transport."""

    # 1. Fetch AI Services
    stt = get_stt_service()
    tts = get_tts_service()
    llm = get_llm_service()

    # 2. Setup VAD (Voice Activity Detection) - Tuned for Noisy Environments
    vad_analyzer = SileroVADAnalyzer(
        params=VADParams(
            confidence=0.65,
            min_volume=0.5,   # Noise Floor: Ignores quiet distant background music
            start_secs=0.2,   # Standard timing
            stop_secs=0.2
        )
    )

    # 3. Setup Context, Prompts, and Tools
    tools_schema = ToolsSchema(standard_tools=tools.hotel_concierge_tools)

    context = LLMContext( 
        messages=[{"role": "system", "content": SYSTEM_PROMPT}],
        tools=tools_schema
    )

    # Register the tools with Gemini
    for tool_func in tools.hotel_concierge_tools:
        llm.register_direct_function(tool_func)

    # 4. Turn-Taking Logic
    context_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=vad_analyzer,
            user_idle_timeout=5.0,
            user_turn_strategies=UserTurnStrategies(
                start=[VADUserTurnStartStrategy()],
                stop=[SpeechTimeoutUserTurnStopStrategy()],
            ),
        ),
    )

    # 🚀 THE UPGRADE: 3-Strike FinOps Kill-Switch
    user_aggregator = context_aggregator.user()
    idle_count = 0  # This resets to 0 for every new phone call!

    @user_aggregator.event_handler("on_user_turn_started")
    async def on_user_turn_started(_aggregator, _strategy):
        """Reset the strike counter if the human actually speaks!"""
        nonlocal idle_count
        if idle_count > 0:
            logger.debug("[User Spoke] Resetting idle counter to 0.")
            idle_count = 0

    @user_aggregator.event_handler("on_user_turn_idle")
    async def on_user_turn_idle(aggregator):
        nonlocal idle_count
        idle_count += 1
        logger.info(f"[Silence Detected] User went idle. Strike {idle_count}/3")
        
        if idle_count >= 3:
            logger.info("[Max Idle Reached] Disconnecting call to prevent Twilio overages.")
            # 1. Bypass the LLM and instantly speak a pre-written goodbye
            await aggregator.push_frame(TTSSpeakFrame("I haven't heard from you in a while, so I am going to disconnect the call. Have a great day!"))
            # 2. Physically sever the Twilio connection
            await aggregator.push_frame(EndFrame())
        else:
            # First or second strike: Polite nudge via the LLM
            nudge_message = {
                "role": "user",
                "content": f"[SYSTEM EVENT]: The caller has been completely silent. This is reminder {idle_count} of 3. Very politely and briefly ask if they are still there."
            }

            await aggregator.push_frame(LLMMessagesAppendFrame([nudge_message], run_llm=True))

    # 5. Build the Pipeline (The Audio Highway)
    pipeline = Pipeline([
        transport.input(),              # Audio from Twilio
        stt,                            # Transcribe to Text
        user_aggregator,                # Add user text to memory
        llm,                            # Generate AI response
        tts,                            # Turn AI text to Audio
        transport.output(),             # Send Audio back to Twilio
        context_aggregator.assistant()  # Add AI response to memory
    ])

    # 🚀 ENABLE LATENCY METRICS AND TOKEN TRACKING
    return PipelineTask(
        pipeline, 
        params=PipelineParams(
            allow_interruptions=True,
            enable_metrics=True,         # Enables latency tracking (TTFB, TTFT)
            enable_usage_metrics=True    # Enables LLM token tracking
        ),
        observers=[MetricsLogObserver()] # Automatically prints metrics to the terminal
    )