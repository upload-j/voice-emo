# prosody_hume.py
import os
from datetime import datetime
import speech_recognition as sr
import requests
import json
import time
import wave
import pyaudio
import asyncio
import aiohttp
import logging
import uuid
from dataclasses import dataclass
from pydub import AudioSegment
import tempfile
from dotenv import load_dotenv
from pathlib import Path
import traceback
import psutil
import random

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
def load_environment():
    """Load environment variables from .env file."""
    try:
        # Try multiple possible locations for the .env file
        possible_paths = [
            Path(__file__).parent / '.env',  # backend/.env
            Path(__file__).parent / '.env.local',  # backend/.env.local
            Path(__file__).parent.parent / 'backend' / '.env',  # ./backend/.env
        ]
        
        env_file = None
        for path in possible_paths:
            if path.exists():
                env_file = path
                logger.info(f"Found .env file at: {path}")
                break
            else:
                logger.debug(f"No .env file at: {path}")
        
        if not env_file:
            paths_tried = '\n  - '.join([str(p) for p in possible_paths])
            logger.error(f"No .env file found in any of these locations:\n  - {paths_tried}")
            raise FileNotFoundError("Required .env file not found. Please ensure it exists in the backend directory.")
        
        # Load and verify environment
        load_dotenv(dotenv_path=env_file, override=True)
        logger.info("Environment file loaded successfully")
        
        # Verify API key is loaded
        api_key = os.getenv('HUME_API_KEY')
        if not api_key:
            raise ValueError("HUME_API_KEY not found in environment variables")
        
        if api_key == "your_hume_api_key_here":
            raise ValueError("HUME_API_KEY contains default value. Please update it with your actual API key.")
        
        # Log API key length (safely)
        logger.info(f"API key loaded (length: {len(api_key)})")
        return True
        
    except Exception as e:
        logger.error(f"Failed to load environment: {str(e)}")
        raise

# Function to refresh environment variables
def refresh_environment():
    """Reload environment variables from .env file."""
    try:
        return load_environment()
    except Exception as e:
        logger.error(f"Failed to refresh environment: {str(e)}")
        return False

@dataclass
class EmotionEmbeddingItem:
    """Represents an emotion with its score from the prosody analysis."""
    name: str
    score: float

def get_api_key():
    key = os.getenv('HUME_API_KEY')
    # Only log the length and first/last 4 chars of the key
    masked_key = f"{key[:4]}...{key[-4:]}" if key else None
    logger.info(f"✅ API Key loaded (length: {len(key) if key else 0})")
    return key

def validate_wav_file(filename):
    """Validate WAV file format."""
    try:
        with wave.open(filename, 'rb') as wav_file:
            # Get WAV file properties
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            framerate = wav_file.getframerate()
            n_frames = wav_file.getnframes()
            duration = n_frames / float(framerate)
            
            print("\nWAV File Properties:")
            print(f"Channels: {channels}")
            print(f"Sample Width: {sample_width * 8} bits")
            print(f"Sample Rate: {framerate} Hz")
            print(f"Duration: {duration:.2f} seconds")
            
            # Check if format matches requirements
            if sample_width != 2:  # 2 bytes = 16 bits
                print("Warning: Sample width should be 16-bit")
            if channels != 1:
                print("Warning: File has multiple channels")
                
            return True
    except Exception as e:
        print(f"Error validating WAV file: {e}")
        return False

def record_audio(duration=5):
    """Record audio using speech_recognition."""
    recognizer = sr.Recognizer()
    with sr.Microphone() as source:
        print("Recording will start in 3 seconds...")
        time.sleep(3)
        print("Recording now...")
        
        # Record audio
        audio = recognizer.listen(source, timeout=duration)
        
        # Save recording locally as a WAV file
        filename = f"recording_{datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"
        with open(filename, "wb") as f:
            f.write(audio.get_wav_data())
            
        print(f"Saved recording to {filename}")
        
        # Add transcription here
        try:
            text = recognizer.recognize_google(audio)
            print(f"Transcribed text: {text}")
            return filename, text  # Return both filename and transcribed text
        except Exception as e:
            print(f"Error transcribing audio: {e}")
            return filename, None  # Return filename even if transcription fails

def transcribe_audio(audio_file: str) -> str:
    """Transcribe audio file to text using Google Speech Recognition."""
    recognizer = sr.Recognizer()
    try:
        # Convert WebM to WAV if the file is WebM
        if audio_file.endswith('.webm'):
            # Load the WebM file
            audio = AudioSegment.from_file(audio_file, format="webm")
            # Create a temporary WAV file
            wav_path = audio_file.replace('.webm', '.wav')
            audio.export(wav_path, format="wav")
            # Use the WAV file for transcription
            with sr.AudioFile(wav_path) as source:
                audio_data = recognizer.record(source)
                text = recognizer.recognize_google(audio_data)
                print(f"Transcribed text: {text}")
                # Clean up the temporary WAV file
                os.remove(wav_path)
                return text
        else:
            # If it's already a WAV file, proceed as before
            with sr.AudioFile(audio_file) as source:
                audio_data = recognizer.record(source)
                text = recognizer.recognize_google(audio_data)
                print(f"Transcribed text: {text}")
                return text
    except sr.UnknownValueError:
        print("Speech Recognition could not understand audio")
        return ""
    except sr.RequestError as e:
        print(f"Could not request results from Speech Recognition service; {e}")
        return ""
    except Exception as e:
        print(f"Error transcribing audio: {e}")
        return ""

async def analyze_prosody(audio_file_path: str) -> dict:
    """
    Analyze prosody in an audio segment using Hume API.
    """
    # Refresh environment variables before processing
    if not refresh_environment():
        raise ValueError("Failed to load valid API key from environment")
    
    # Get fresh API key
    api_key = os.getenv('HUME_API_KEY')
    if not api_key:
        raise ValueError("API key not found after environment refresh")

    temp_wav = os.path.join(tempfile.gettempdir(), f'tmp{uuid.uuid4().hex}.wav')
    try:
        # Export audio with correct parameters
        audio = AudioSegment.from_file(audio_file_path)
        audio.export(temp_wav, format='wav', parameters=["-ac", "1", "-ar", "44100"])
        logger.info(f"💾 Saved audio to {temp_wav}")
        
        # Debug API key
        key_length = len(api_key)
        key_prefix = api_key[:4] if key_length >= 4 else ""
        key_suffix = api_key[-4:] if key_length >= 4 else ""
        logger.info(f"🔑 API Key Debug - Length: {key_length}, Format: {key_prefix}...{key_suffix}")
        
        # Set up API request
        headers = {
            "accept": "application/json",
            "X-Hume-Api-Key": api_key
        }
        url = "https://api.hume.ai/v0/batch/jobs"
        
        # Debug headers
        logger.info(f"📨 Headers Debug - Keys present: {', '.join(headers.keys())}")
        
        # Create request payload with minimal settings
        config = {
            "models": {
                "prosody": {
                    "granularity": "utterance"
                }
            }
        }
        
        # Create FormData once
        data = aiohttp.FormData()
        data.add_field('json', json.dumps(config))
        data.add_field('file', open(temp_wav, 'rb'), filename='audio.wav', content_type='audio/wav')
        
        async with aiohttp.ClientSession() as session:
            # Track submission time
            submit_start = time.time()
            
            # Submit job with debug logging
            logger.info("📤 Submitting prosody analysis job...")
            
            async with session.post(url, headers=headers, data=data) as response:
                response_text = await response.text()
                logger.info(f"📥 Response status: {response.status}")
                logger.info(f"📥 Response headers: {response.headers}")
                logger.info(f"📥 Response text: {response_text[:200]}...")  # Log first 200 chars of response
                
                if response.status != 200:
                    raise Exception(f"Failed to submit job: {response_text}")
                
                response_data = json.loads(response_text)
                job_id = response_data["job_id"]
                submit_time = time.time() - submit_start
                logger.info(f"✅ Job submitted. ID: {job_id}")
                
                # Track polling time
                poll_start = time.time()
                
                # Poll for completion with timeout
                timeout = time.time() + 30  # 30 second timeout
                base_delay = 0.1  # Start with 100ms delay
                max_delay = 2.0   # Cap at 2 seconds
                attempt = 0
                
                while time.time() < timeout:
                    async with session.get(f"{url}/{job_id}", headers=headers) as status_response:
                        if status_response.status != 200:
                            error_text = await status_response.text()
                            raise Exception(f"Failed to get job status: {error_text}")
                        
                        status_data = await status_response.json()
                        state = status_data["state"]["status"]
                        logger.info(f"Job state: {state}")
                        
                        if state == "COMPLETED":
                            break
                        elif state == "FAILED":
                            raise Exception("Prosody analysis job failed")
                        
                        # Calculate delay with exponential backoff and jitter
                        delay = min(base_delay * (2 ** attempt), max_delay)
                        jitter = delay * 0.2 * random.random()  # Add up to 20% jitter
                        delay_with_jitter = delay + jitter
                        
                        logger.info(f"Polling attempt {attempt + 1}: waiting {delay_with_jitter:.2f}s")
                        await asyncio.sleep(delay_with_jitter)
                        attempt += 1
                else:
                    raise Exception("Job timed out after 30 seconds")
                
                poll_time = time.time() - poll_start
                
                # Track prediction fetch time
                predict_start = time.time()
                
                # Get predictions
                async with session.get(f"{url}/{job_id}/predictions", headers=headers) as pred_response:
                    if pred_response.status != 200:
                        error_text = await pred_response.text()
                        raise Exception(f"Failed to get predictions: {error_text}")
                    
                    predictions = await pred_response.json()
                    predict_time = time.time() - predict_start
                    
                    if not predictions:
                        logger.warning("No predictions returned from the API")
                        return {
                            "emotions": [],
                            "duration": len(audio) / 1000.0,
                            "timing": {
                                "submit": submit_time,
                                "poll": poll_time,
                                "predict": predict_time
                            }
                        }
                    
                    try:
                        # Process predictions
                        if not predictions or len(predictions) == 0:
                            logger.warning("No predictions in response array")
                            return {
                                "emotions": [],
                                "duration": len(audio) / 1000.0,
                                "timing": {
                                    "submit": submit_time,
                                    "poll": poll_time,
                                    "predict": predict_time
                                }
                            }

                        prediction = predictions[0]
                        if not prediction or "results" not in prediction:
                            logger.warning("Invalid prediction format")
                            return {
                                "emotions": [],
                                "duration": len(audio) / 1000.0,
                                "timing": {
                                    "submit": submit_time,
                                    "poll": poll_time,
                                    "predict": predict_time
                                }
                            }

                        results = prediction.get("results", {})
                        predictions_array = results.get("predictions", [])
                        if not predictions_array:
                            logger.warning("No predictions array in results")
                            return {
                                "emotions": [],
                                "duration": len(audio) / 1000.0,
                                "timing": {
                                    "submit": submit_time,
                                    "poll": poll_time,
                                    "predict": predict_time
                                }
                            }

                        prosody_data = predictions_array[0].get("models", {}).get("prosody", {})
                        grouped_predictions = prosody_data.get("grouped_predictions", [])
                        
                        if grouped_predictions and grouped_predictions[0].get("predictions"):
                            emotions_data = grouped_predictions[0]["predictions"][0].get("emotions", [])
                            emotions = [
                                EmotionEmbeddingItem(
                                    name=emotion["name"],
                                    score=emotion["score"]
                                )
                                for emotion in emotions_data
                            ]
                        else:
                            logger.warning("No emotions found in grouped predictions")
                            emotions = []

                        total_time = submit_time + poll_time + predict_time
                        audio_duration = len(audio) / 1000.0  # Convert to seconds

                        # Print clear analysis summary
                        print("\n" + "="*50)
                        print("🎯 Analysis Summary:")
                        print(f"   - Audio Duration: {audio_duration:.1f}s")
                        print("   - Timing Breakdown:")
                        print(f"     • Submit: {submit_time:.2f}s")
                        print(f"     • Poll: {poll_time:.2f}s")
                        print(f"     • Predict: {predict_time:.2f}s")
                        print(f"     • Total: {total_time:.2f}s")
                        print(f"   - Emotions Found: {len(emotions)}")
                        if emotions:
                            print("   - Top 3 Emotions:")
                            sorted_emotions = sorted(emotions, key=lambda x: x.score, reverse=True)[:3]
                            for emotion in sorted_emotions:
                                print(f"     • {emotion.name}: {emotion.score:.3f}")
                        print("="*50 + "\n")
                        
                        return {
                            "emotions": emotions,
                            "duration": audio_duration,
                            "timing": {
                                "submit": submit_time,
                                "poll": poll_time,
                                "predict": predict_time,
                                "total": total_time
                            }
                        }
                        
                    except Exception as e:
                        logger.error(f"Error processing predictions: {str(e)}")
                        raise Exception(f"Failed to process predictions: {str(e)}")
    except Exception as e:
        error_msg = str(e)
        if "<!DOCTYPE html>" in error_msg:
            # Extract just the error code and message
            if "520: Web server is returning an unknown error" in error_msg:
                error_msg = "Hume API server error (520). Please try again in a few minutes."
            else:
                error_msg = "Hume API server error. Please try again later."
        logger.error(f"❌ Error in prosody analysis: {error_msg}")
        raise Exception(error_msg)
    finally:
        # Clean up temporary file
        if os.path.exists(temp_wav):
            os.remove(temp_wav)
            logger.info("🧹 Cleaned up temporary file")

async def main():
    # Record an audio sample from the microphone
    audio_file, text = record_audio()
    if audio_file:
        # Submit the recorded file for prosody analysis
        await analyze_prosody(audio_file)

if __name__ == "__main__":
    asyncio.run(main()) 