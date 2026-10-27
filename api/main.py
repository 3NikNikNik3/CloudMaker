from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi import status
from fastapi.exceptions import HTTPException
from pydantic import BaseModel
from fastapi import UploadFile, Form, File
from fastapi.responses import FileResponse, RedirectResponse

import os

from typing import Annotated

from dataclasses import dataclass

import subprocess
import signal

import json


SECONDWAITSTOP = 10
MAXMEM = 6 << 10
MAXCPU = 4


@dataclass
class VM:
	proc: subprocess.Popen
	mem: int
	cpu: int
	disk: str
	run: bool

use_mem = 0
use_cpu = 0
vms = []

@dataclass
class Procy:
	proc: subprocess.Popen
	port: int

procy = None

@asynccontextmanager
async def lifespan(app: FastAPI):
	global procy
	try:
		procy = Procy(
			subprocess.Popen(['socat', 'TCP-LISTEN:8999,reuseaddr,fork', 'TCP:127.0.0.1:9000']),
			9000
		)
	except Exception as e:
		print('Error:', e)
		raise e

	try:
		MAXMEM = int(os.getenv("MAX_MEM", MAXMEM))
	except: pass

	try:
		MAXCPU = int(os.getenv("MAX_CPU", MAXCPU))
	except: pass

	yield

	for i in vms:
		i.proc.send_signal(signal.SIGINT)

	for i in vms:
		try:
			i.proc.wait(SECONDWAITSTOP)
		except subprocess.TimeoutExpired:
			print("VM don't stop, I kill it")
			i.proc.kill()

	try:
		procy.proc.send_signal(signal.SIGINT)
		procy.proc.wait(SECONDWAITSTOP)
	except subprocess.TimeoutExpired:
		procy.proc.kill()

app = FastAPI(lifespan=lifespan)


def is_ok_path(path: str):
	if '../' in path or '/..' in path:
		return False

	for i in path:
		if not (i.isalpha() or i.isnumeric() or i == '.' or i == '_' or i == ' '):
			return False
	return True

def updata_vm(i: int):
	global use_mem, use_cpu

	if vms[i].run and vms[i].proc.poll() is not None:
		use_mem -= vms[i].mem
		use_cpu -= vms[i].cpu
		vms[i].run = False


# VM

class CreateVm(BaseModel):
	mem: str
	cpu: int
	disk: str

@app.post('/vm')
async def create_vm(how: Annotated[CreateVm, Form()]):
	global use_mem, use_cpu

	# check mem
	if not (how.mem.isnumeric() or (how.mem[-1] in 'GM' and how.mem[:-1].isnumeric())):
		raise HTTPException(400, "Please, use memory like 1024, 1024M, 1G")

	if how.mem.isnumeric():
		mem = int(how.mem)
	else:
		mem = int(how.mem[:-1])
		if how.mem[-1] == 'G':
			mem <<= 10
	# check cpu
	if how.cpu <= 0:
		raise HTTPException(400, "Please, use cpu more then 0")

	# check limit
	if MAXMEM < use_mem + mem or MAXCPU < use_cpu + how.cpu:
		raise HTTPException(400, "Too big mem or cpu need")

	# check disk
	if not is_ok_path(how.disk) or not os.path.exists('data/' + how.disk) or not os.path.exists('data/' + how.disk + '.desc'):
		raise HTTPException(404, "I don't have this disk")

	for i in range(len(vms)):
		updata_vm(i)
		if vms[i].run and vms[i].disk == how.disk:
			raise HTTPException(400, "This disk is used")

	# run
	try:
		id = len(vms)

		vms.append(VM(
			subprocess.Popen([
				"qemu-system-x86_64",
				"-m", str(mem),
				"-smp", str(how.cpu),
				"-nographic",
				"-enable-kvm",
				"-drive", "file=data/" + how.disk,
				"-nic", f"user,hostfwd=tcp::{9000 + id}-:22"
			], stdout=subprocess.DEVNULL),
			mem, how.cpu, how.disk,
			True
		))

		use_mem += mem
		use_cpu += how.cpu

	except Exception as e:
		print('Error:', e)
		raise HTTPException(500, "I can't")
	return '{id = ' + str(id) + '}'

class HowDelete(BaseModel):
	id: int
	kill: bool

@app.delete('/vm')
async def delete_vm(how: Annotated[HowDelete, Form()]):
	if how.id >= len(vms):
		raise HTTPException(400, "id > count of VMs")
	elif how.id < 0:
		raise HTTPException(400, "id < 0 ._.")

	if how.kill:
		vms[how.id].proc.kill()
		updata_vm(how.id)
	else:
		vms[how.id].proc.send_signal(signal.SIGINT)

	return 'I send'

@app.get('/vm')
async def info_vms():
	arr = ''

	for i in range(len(vms)):
		updata_vm(i)
		if vms[i].run:
			arr += '{' + \
				f'"id":{i},' + \
				f'"mem":{vms[i].mem},' + \
				f'"cpu":{vms[i].cpu},' + \
				f'"disk":{vms[i].disk}' + \
			'},'

	return '{"arr": [' + arr[:-1] + ']' + f', "use_mem": {use_mem}, "use_cpu": {use_cpu}' + '}'


# disk

class DiskData(BaseModel):
	name: str
	desc: str

@app.post('/disk')
async def load_disk(file: Annotated[UploadFile, File()], data: Annotated[DiskData, Form()]):
	if len(data.name) < 6 or data.name[-6:] != '.qcow2':
		return HTTPException(400, "Please, give disk formated .qcow2")

	if data.name == '' or not is_ok_path(data.name):
		return HTTPException(400, "Bad name, only alpha, number, ., _ and space")

	try:
		with open(f'data/{data.name}', 'wb') as fd:
			while buf := await file.read(1<<20):
				fd.write(buf)
	except:
		os.system(f'rm /data/{data.name}')

		return HTTPException(500, "Cann't load disk :(")

	await file.close()

	with open(f'data/{data.name}.desc', 'w') as fd:
		fd.write(data.desc)

	return "All load"

@app.get('/disk')
async def get_dists():
	ans = ''

	for i in os.listdir('data/'):
		if len(i) >= 5 and i[-5:] == '.desc' and os.path.exists('data/' + i[:-5]):
			ans += '{"name":"' + i[:-5] + '","desc":"'

			with open('data/' + i, 'r') as file:
				ans += file.read()

			ans += '"},'

	return '{"arr":[' + ans[:-1] + ']}'

@app.delete('/disk')
async def delete_disk(what: Annotated[str, Form()]):
	if not is_ok_path(what) or not os.path.exists('data/' + what) or not os.path.exists('data/' + what + '.desc'):
		print(is_ok_path(what), os.path.exists('data/' + what), os.path.exists('data/' + what + '.desc'))
		return HTTPException(400, "I don't have this disk ._.")

	try:
		os.system(f'rm data/{what}')
		os.system(f'rm data/{what}.desc')
	except:
		return HTTPException(500, "Strange, I can not...")

	return 'All!'

@app.get('/disk/download/{path}')
def download_disk(path: str):
	if not is_ok_path(path) or not os.path.exists(f'data/{path}.desc'):
		return HTTPException(404, "I don't have this disk")

	return FileResponse(path='data/' + path, filename=path, media_type="application/octet-stream")

class CopyData(BaseModel):
	from_: str
	to:str
	new_desc: str

@app.post('/disk/copy')
def copy_disk(data: Annotated[CopyData, Form()]):
	if not is_ok_path(data.from_) or not is_ok_path(data.to):
		return HTTPException(400, "Please, use real path")

	if not os.path.exists('data/' + data.from_) or not os.path.exists('data/' + data.from_ + '.desc'):
		return HTTPException(404, "I don't have disk on \"from\"")

	if os.path.exists('data/' + data.to):
		return HTTPException(400, "I have disk on \"to\"")

	try:
		os.system(f'cp data/{data.from_} data/{data.to}')
	except Exception as e:
		print('Error:', e)
		return HTTPException(500, "I can't ._.")

	try:
		with open(f'data/{data.to}.desc', 'w') as file:
			file.write(data.new_desc)
	except Exception as e:
		print('Error:', e)

		os.system(f'rm data/{data.to}')
		os.system(f'rm data/{data.to}.desc')
		return HTTPException(500, "I can't ._.")

	return "Complete"


# connect (port)

@app.get('/connect')
def get_connect_vm():
	return procy.port - 9000

@app.put('/connect')
def set_connect(id: Annotated[int, Form()]):
	if id < 0 or id >= len(vms):
		return HTTPException(400, "id from 0 to count of vm")

	updata_vm(id)
	if not vms[id].run:
		return HTTPException(404, "I don't have vm with id=" + str(id))

	try:
		procy.proc.send_signal(signal.SIGINT)
		procy.proc.wait(SECONDWAITSTOP)
	except subprocess.TimeoutExpired:
		procy.proc.kill()

	procy.proc = subprocess.Popen(['socat', 'TCP-LISTEN:8999,reuseaddr,fork', f'TCP:127.0.0.1:{9000 + id}'])
	procy.port = id

	return 'Complite'


@app.get("/")
async def index():
	return RedirectResponse(url='/docs')
