# How to set up a dev instance

These instructions are intended more as a jump-start on relevant yak-shaving, rather than a complete and correct guide. On Mac, I worked through PyCharm Pro.  Equivalent setup via the Terminal, VSCode, etc shouldn’t be too hard(?)

## Install Stuff
### Ubuntu
```shell
sudo apt update
sudo apt install python3-venv python3-dev libpq-dev postgresql postgresql-contrib curl

# Start up the SQL interface to set up the database
sudo -u postgres psql
```
### Mac
You will probably have the smoothest path with python 3.12 (new enough to support all the requirements, not so new that you have to build anything from source), which probably is newer than your OS has pre-installed.  PyCharm lets you pick version (and install if needed) when you set up a virtual environment, but you should also be able to use something like `brew install python@3.12` 

[Postgres.app](https://postgresapp.com/) is easy to use.  
 - Drag it to your Applications folder, and run it
 - Click "Initialize"  
 - "Connect" to open the `psql` interface  

Once you're in `psql`
```
CREATE DATABASE puzzle_db;
CREATE USER puzzle WITH PASSWORD ‘plantsandbirdsandrocksandthings’;
ALTER ROLE puzzle SET client_encoding TO 'utf8';
ALTER ROLE puzzle SET default_transaction_isolation TO 'read committed';
ALTER ROLE puzzle SET timezone to 'UTC';
ALTER DATABASE puzzle_db OWNER TO puzzle;
\q
```
and you should be good to go (modulo maybe needing to allow connections when asked)

## Get the code
IDEs will probably integrate talking to GitHub in a more friendly way, but
with `git` from the command line
```shell
git clone https://github.com/acoth/metaphysical-solving.git
cd metaphysical-solving

# grab the zulip submodule (the second step takes a while)
git submodule init
git submodule update
```
and potentially `git switch some-branch-you-want-to-try`

## Set up the virtual environment
Again, an IDE will probably have a friendly way of doing this.  The command-line version is (assuming you're still in the metaphysical-solving directory:
```shell
#Create a virtual env
python3 -m venv ./env

#Activate it
source env/bin/activate

#Get all the things!
pip install -r requirements.txt
```

## Configuration Files
There are some configuration data (secret keys to do Google Auth, post to Zulip, etc) and installation-specific stuff which is not included in the git repo, but *must* be present for the server to function.
In particular there is a `configuration` directory which includes these secrets, and you need a `solving/local_settings.py` as well.

Since this tends to be the same for a number of installations, I just copy the directory from an existing setup (e.g. the production one)
```shell
# Example assuming you have SSH access to the prod server at puzzle-solv.ing
scp -r puzzle@puzzle-solv.ing:metaphysical-solving/configuration .
ln -s configuration/local_settings.py solving/local_settings.py
```

## Final Setup
```shell
# from within the virtual env you setup above
python manage.py migrate
python manage.py initial_config
```

Google OAuth (and maybe other things?) are set up to accept access from a site at a specific port on localhost, 8080 in our case.
If this is on your local machine, you can just
```
python manage.py runserver 8080` 
```
but if it's a machine you're sshed into, you will need to (log out and re-)connect with the relevant port forwarded over ssh:
```shell
ssh -L8080:localhost:8080 puzzle@dev.example.com
```
Finally, point your web browser to `https://localhost:8080` and you should be good!