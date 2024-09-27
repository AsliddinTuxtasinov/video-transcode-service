import os
import hashlib
from fastapi import FastAPI, File, UploadFile, BackgroundTasks, Request, HTTPException
from fastapi.responses import StreamingResponse, Response, JSONResponse
import ffmpeg
from pathlib import Path

# Create FastAPI app instance
app = FastAPI()

# Directories for storing uploaded and transcoded files
UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", "./uploads")
TRANSCODED_FOLDER = os.getenv("TRANSCODED_FOLDER", "./transcoded")

# Ensure directories exist
Path(UPLOAD_FOLDER).mkdir(parents=True, exist_ok=True)
Path(TRANSCODED_FOLDER).mkdir(parents=True, exist_ok=True)

# Dictionary to keep track of transcoding status
transcoding_status = {}


def generate_file_hash(file_path: str) -> str:
    """Generate a unique hash for a file based on its content."""
    hash_sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_sha256.update(chunk)
    return hash_sha256.hexdigest()


def transcode_video(input_file: str, output_file: str):
    """Transcode video to 720p resolution using FFmpeg."""
    file_hash = os.path.basename(output_file).replace("transcoded_", "").replace(".mp4", "")
    transcoding_status[file_hash] = "in_progress"
    try:
        ffmpeg.input(input_file).output(output_file, vcodec='libx264', vf='scale=1280x720').run()
        transcoding_status[file_hash] = "completed"
    except ffmpeg.Error as e:
        transcoding_status[file_hash] = "failed"
        print(f"Error transcoding video: {e}")


@app.post("/upload/")
async def upload_file(file: UploadFile = File(...), background_tasks: BackgroundTasks = BackgroundTasks()):
    # Save uploaded file
    upload_path = os.path.join(UPLOAD_FOLDER, file.filename)
    with open(upload_path, 'wb') as f:
        f.write(await file.read())

    # Generate file hash
    file_hash = generate_file_hash(upload_path)
    transcoded_filename = f"transcoded_{file_hash}.mp4"
    output_file = os.path.join(TRANSCODED_FOLDER, transcoded_filename)

    # Start transcoding in the background
    background_tasks.add_task(transcode_video, upload_path, output_file)

    return JSONResponse(status_code=200,
                        content={"message": "Video uploaded, transcoding in progress", "file_hash": file_hash})


@app.get("/stream/{file_hash}")
async def stream_video(file_hash: str, request: Request):
    transcoded_file = os.path.join(TRANSCODED_FOLDER, f"transcoded_{file_hash}.mp4")

    if not os.path.exists(transcoded_file):
        raise HTTPException(status_code=404, detail="File not found")

    file_size = os.path.getsize(transcoded_file)

    range_header = request.headers.get("range")
    if range_header:
        start, end = range_header.replace("bytes=", "").split("-")
        start = int(start)
        end = int(end) if end else file_size - 1

        def iter_file(start: int, end: int):
            with open(transcoded_file, "rb") as video_file:
                video_file.seek(start)
                yield video_file.read(end - start + 1)

        return StreamingResponse(
            iter_file(start, end),
            status_code=206,
            headers={
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(end - start + 1),
                "Content-Type": "video/mp4",
            },
        )

    # If no range header, stream the entire file
    return StreamingResponse(open(transcoded_file, "rb"), media_type="video/mp4")
