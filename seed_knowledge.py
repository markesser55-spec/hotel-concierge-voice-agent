"""
Seed hotel policy embeddings (RAG)
=================================
Reads `hotel_policy.txt`, dynamically filters out major headers, isolates 
subsections, embeds them with the new 'gemini-embedding-001' model, and pushes to Supabase.
"""

import os
import re
import asyncio
from dotenv import load_dotenv
from supabase import create_client, Client
from google import genai
from google.genai import types as genai_types

load_dotenv()

# Initialize Supabase
url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(url, key)

# Initialize Google GenAI
client = genai.Client()

EMBEDDING_MODEL = "gemini-embedding-001"
DOCUMENT_PATH = "hotel_policy.txt"
HOTEL_NAME = "The Grand Horizon Resort & Spa"

def chunk_text_document(filepath: str) -> list[str]:
    """
    Reads the text file and splits it cleanly at every numbered header.
    It utilizes a 'Smart Filter' to ignore short category titles and metadata.
    """
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"[Error] Could not find '{filepath}'.")

    with open(filepath, "r", encoding="utf-8") as f:
        clean_text = f.read()

    # THE MAGIC REGEX: Splits at a newline followed by "X. " OR "X.X "
    raw_chunks = re.split(r'\n+(?=\d+\.\s|\d+\.\d+\s)', clean_text.strip())

    valid_chunks = []
    for chunk in raw_chunks:
        clean_chunk = chunk.strip()

        # SMART FILTER: Ignore titles and empty headers < 50 chars
        if len(clean_chunk) > 50 and not clean_chunk.startswith("[Page 1]"):
            # Context enrichment: Anchor every chunk to the hotel name
            enriched_chunk = f"{HOTEL_NAME} Policy:\n{clean_chunk}"
            valid_chunks.append(enriched_chunk)

    print(f"[Parser] Successfully isolated {len(valid_chunks)} specific policy chunks.")
    return valid_chunks

async def seed_db() -> None:
    """
    Clear existing policy rows, chunk the policy document, embed each chunk with
    Gemini, and insert content + vectors into Supabase for RAG.
    """
    print(f"\n[Booting Vector Ingestion Engine for {DOCUMENT_PATH}...]")

    try:
        chunks = chunk_text_document(DOCUMENT_PATH)

        print("[Database] Clearing old policies...")
        supabase.table("hotel_policies").delete().neq("id", 0).execute()

        for i, chunk in enumerate(chunks):
            # Generate the math vector using the new model
            response = await client.aio.models.embed_content(
                model=EMBEDDING_MODEL,
                contents=chunk,
                config=genai_types.EmbedContentConfig(
                    task_type="RETRIEVAL_DOCUMENT",
                    # 🚀 MRL Magic: The new model natively scales down to 768 to fit our DB!
                    output_dimensionality=768 
                ),
            )
            embedding = response.embeddings[0].values

            # Push the text chunk and the math coordinates to Supabase
            supabase.table("hotel_policies").insert({
                "content": chunk,
                "embedding": embedding
            }).execute()

            # Print a clean terminal preview
            preview = chunk.replace('\n', ' ')[:80]
            print(f"✅ Uploaded Chunk {i+1}: {preview}...")

            await asyncio.sleep(0.2) # API rate limit protection

        print(f"\n[✅ Success] {len(chunks)} custom policy chunks are live in Supabase!")

    except Exception as e:
        print(f"\n[❌ System Error] {str(e)}")

if __name__ == "__main__":
    asyncio.run(seed_db())