from aparat import Aparat

username = "_TBMG_"
password = "mahmahk81"

aparat = Aparat()

user = aparat.login(username, password)
form = aparat.uploadForm(user.username, user.ltoken)

video = aparat.uploadPost(
    form=form,
    video_path='video.mp4',
    title='title',
    category=3,
    tags=['english', 'learn english', 'teaching'],
    allow_comment=True,
    descreption='',
    video_pass=False
)
print("helooooo")
print(video)
