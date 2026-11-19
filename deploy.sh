#!/bin/sh -xe

if [ puzzle != $(whoami) ]; then
    exec sudo -u puzzle "$0" "$*"
fi

cd /home/puzzle/metaphysical-solving
git pull
. env/bin/activate
yes yes | python manage.py collectstatic # sigh

#Apache version
#touch solving/wsgi.py

# Nginx/Gunicorn version (access to this single command must be
# granted via sudoers, which can be done by (as root) copying the
# included allow-gunicorn-restart to /etc/sudoers.d)
sudo /usr/bin/systemctl restart gunicorn