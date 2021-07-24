from flask import Flask, render_template, url_for, request, Response, redirect, flash
from cv2 import cv2
import numpy as np

app = Flask(__name__)

URL = "http://cnt.dontexist.com:8080/video"

camera = cv2.VideoCapture(URL)

@app.route('/', methods=['POST','GET'])
def index():
        if request.method == 'POST':
                password = request.form['password']

                if password == '123':
                        return redirect('/home')
                else:
                        return redirect('/')

        return render_template('index.html')


@app.route('/home', methods=['GET'])
def home():
        return render_template('home.html')


@app.route('/staticv',methods=['POST','GET'])
def staticv():
        return render_template('staticvid.html')

#inspiration from https://blog.miguelgrinberg.com/post/flask-video-streaming-revisited/page/3
def gen():
        while True:
                ret, image = camera.read()
                if not ret:
                        break
                else:
                        _, encodedImage = cv2.imencode('.jpg',image)
                        yield (b'--frame\r\n' b'Content-Type: image/jpeg\r\n\r\n' + bytearray(encodedImage) + b'\r\n')
                #q = cv2.waitKey(30)
                #if q == ord("q"):
                #        break
        camera.release()

#inspiration from https://stackoverflow.com/questions/60509538/how-do-i-stream-python-opencv-output-to-html-canvas




@app.route('/streamvid',methods=['POST','GET'])
def streamvid():
        if request.method == 'GET':
                return Response(gen(), mimetype='multipart/x-mixed-replace; boundary=frame')
        else:
                return render_template('streamvid.html')

if __name__ == "__main__":
        app.run(debug=True)
