FROM pytorch/pytorch:2.0.1-cuda11.7-cudnn8-runtime

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY models/ /app/models/
COPY predict.py .

CMD ["python", "predict.py"]
