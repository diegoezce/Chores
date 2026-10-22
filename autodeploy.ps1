# Si hay commits nuevos en origin/main, los baja y reconstruye el contenedor.
# Pensado para el Programador de tareas de Windows (ver README).
Set-Location $PSScriptRoot
git fetch -q origin main
if ($LASTEXITCODE -ne 0) { exit 1 }
if ((git rev-parse HEAD) -ne (git rev-parse origin/main)) {
  Write-Output "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') actualizando a $(git rev-parse --short origin/main)"
  git pull -q --ff-only origin main
  if ($LASTEXITCODE -eq 0) { docker compose up -d --build }
}
