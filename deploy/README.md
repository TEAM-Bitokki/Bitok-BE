# 배포

EC2 한 대에 uvicorn + Postgres(도커). 리전 `ap-northeast-2`.

## 1. EC2

Ubuntu 24.04 · `t3.micro` · 20 GiB gp3 · 키 페어 새로 생성

보안 그룹 인바운드

| 포트 | 소스 |
| --- | --- |
| 22 | 내 IP |
| 8000 | 팀원 IP |
| 5432 | 열지 않음 |

탄력적 IP 를 할당해 연결한다.

HTTPS 가 아니라서 `deviceToken` 이 평문으로 오간다. 그래서 8000 을 전체 공개하지 않는다.
앱 연동 전에 도메인 + Caddy 로 TLS 를 붙이고 443 만 연다.

## 2. 설치

```bash
sudo apt update && sudo apt install -y python3-venv docker.io docker-compose-v2 git
sudo usermod -aG docker ubuntu && exit
```

```bash
git clone -b dev https://github.com/TEAM-Bitokki/Bitok-BE.git
cd Bitok-BE
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env

docker compose up -d --wait
.venv/bin/alembic upgrade head
```

`.env` 에서 로컬과 달라지는 값

| 키 | 값 |
| --- | --- |
| `DEBUG` | `false` |
| `POSTGRES_PASSWORD` | 긴 임의 문자열 |
| `DATABASE_URL` | 위 비밀번호와 일치 |
| `DOCS_PASSWORD` | 아무 문자열 (프론트에 전달) |
| `GEMINI_API_KEY` · `KAKAO_REST_API_KEY` | 발급받은 키 |

스왑 (메모리 1GB 라 OOM 대비)

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

## 3. 서비스 등록

```bash
sudo cp deploy/bitok.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now bitok
```

재부팅 후 `curl localhost:8000/health` 가 바로 되는지 확인

로그: `sudo journalctl -u bitok -f`

## 4. 알림

| | 설정 |
| --- | --- |
| 크레딧 소진 | Billing → Budgets, $30, 50/80/100% |
| 인스턴스 중단 | EC2 → 상태 검사 → 경보 생성, `StatusCheckFailed` |

## 5. 갱신

```bash
cd ~/Bitok-BE && git pull
.venv/bin/pip install -r requirements.txt
.venv/bin/alembic upgrade head
.venv/bin/python -m scripts.retag_ingredients
sudo systemctl restart bitok
```

## 6. 백업

```bash
docker compose exec -T db pg_dump -U bitok bitok | gzip > ~/bitok-$(date +%F).sql.gz
gunzip -c ~/bitok-2026-11-01.sql.gz | docker compose exec -T db psql -U bitok -d bitok
```
