.PHONY: sync run setup logs

sync:
	./scripts/sync.sh

run: sync
	ssh me322@pibot 'cd ~/pi-rocky && . .venv/bin/activate && python rocky.py'

setup:
	rsync -av scripts/ me322@pibot:/home/me322/pi-rocky/scripts/
	rsync -av requirements.txt me322@pibot:/home/me322/pi-rocky/
	ssh me322@pibot 'cd ~/pi-rocky && bash scripts/setup_pi.sh'

logs:
	ssh me322@pibot 'tail -f ~/pi-rocky/rocky.log'
