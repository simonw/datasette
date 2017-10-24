FROM python:3
COPY . /app
WORKDIR /app
RUN pip install -r requirements.txt
RUN python app.py --build
EXPOSE 8006
CMD ["python", "app.py"]
