import os
import logging
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field # Pydantic V2 style
from typing import List, Optional, Dict, Any
import google.generativeai as genai
from dotenv import load_dotenv
import asyncio # Para ejecutar tareas en segundo plano
import time
import redis 
from supabase import create_client, Client


load_dotenv()
url: str = os.getenv("SUPABASE_URL")
key: str = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(url, key)

data = (supabase.table("database_prueba").insert({"id": 1, "name": "Alex", "age": 24}).execute())

# Assert we pulled real data.
assert len(data.data) > 0

