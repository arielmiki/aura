.PHONY: sync run-local run-pi setup-mac setup-pi logs

sync:
	./scripts/sync.sh

run-local:
	. .venv-mac/bin/activate && uvicorn rocky:app --reload --port 8000

run-pi: sync
	ssh me322@pibot 'cd ~/pi-rocky && . .venv/bin/activate && uvicorn rocky:app --host 0.0.0.0 --port 8000'

setup-mac:
	bash scripts/setup_mac.sh

setup-pi:
	rsync -av scripts/ me322@pibot:/home/me322/pi-rocky/scripts/
	rsync -av requirements.txt me322@pibot:/home/me322/pi-rocky/
	ssh me322@pibot 'cd ~/pi-rocky && bash scripts/setup_pi.sh'

logs:
	ssh me322@pibot 'tail -f ~/pi-rocky/rocky.log'
