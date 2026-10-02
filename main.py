# main.py
from fastapi import FastAPI, UploadFile
from model import store_file

app = FastAPI()

@app.post("/api/file_input")
async def file_input(file: UploadFile):
    return {"stored_at": str(store_file(file.filename or "upload", await file.read()))}



    
    






        
