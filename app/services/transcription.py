import os
from openai import AsyncOpenAI

# Initialize the client. Ensure load_dotenv() has been called in your main script first.
openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

async def transcribe_audio(file_path: str) -> str:
    """
    Takes a path to an audio file and returns the transcribed text.
    Includes custom vocabulary for context and robust error handling.
    """
    try:
        with open(file_path, "rb") as audio_stream:
            transcript = await openai_client.audio.transcriptions.create(
                model="whisper-1", 
                file=audio_stream,
                langauage="en",
                # The prompt acts as a hint for the AI to recognize specific words
                prompt="McDonalds, McChicken, Warung, GoJek, Grab, IDR, SGD, USD, AUD, YouTrip" 
            )
        return transcript.text
    
    except Exception as e:
        print(f"❌ OpenAI API Error: {e}")
        return "ERROR: Could not transcribe audio. Please try again."