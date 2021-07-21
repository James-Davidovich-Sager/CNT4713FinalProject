from flask import Flask, render_template, url_for, request, Response, redirect, flash
import cv2

app = Flask(__name__)

camera = cv2.VideoCapture(0)


@app.route('/', methods=['POST','GET'])
def index():
        if request.method == 'POST':
                username = request.form['username']
                password = request.form['password']

                if username == 'user' and password == '123':
                        return render_template('home.html')
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
                if ret:
                        cv2.imwrite('t.jpg', image)
                        yield (b'--frame\r\n'
                               b'Content-Type: image/jpeg\r\n\r\n' + open('t.jpg', 'rb').read() + b'\r\n')
        camera.release()

#inspiration from https://stackoverflow.com/questions/60509538/how-do-i-stream-python-opencv-output-to-html-canvas

@app.route('/streamvid',methods=['POST','GET'])
def stream():
        return Response(gen(),
                        mimetype='multipart/x-mixed-replace; boundary=frame')

if __name__ == "__main__":
        app.run(debug=True)
