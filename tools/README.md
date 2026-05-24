# tools/

Scripts PowerShell para gestionar el bot desplegado en el VPS desde local.

## Requisitos

- Windows con OpenSSH client (`ssh.exe` en PATH)
- SSH key en `~/.ssh/id_ed25519_buysell365` instalada en el VPS
  (en `/root/.ssh/authorized_keys`)

## Scripts disponibles

### `..\deploy.ps1`  *(en la raiz)*
Pipeline completo: commit local → push GitHub → pull VPS → restart.

```powershell
.\deploy.ps1 "fix: descripcion"           # deploy normal
.\deploy.ps1 -DryRun                      # preview sin tocar nada
.\deploy.ps1 -SkipGit                     # solo redeploy desde VPS sin commit
.\deploy.ps1 -Branch migration/foo "msg"  # deploy a otro branch
```

### `vps_ssh.ps1`
SSH interactivo o ejecutar un comando puntual.

```powershell
.\tools\vps_ssh.ps1                                    # sesion interactiva
.\tools\vps_ssh.ps1 "systemctl status buysell365"      # un comando
.\tools\vps_ssh.ps1 "tail -100 /opt/buysell365/logs/bot.log"
```

### `vps_logs.ps1`
Logs del bot en vivo o ultimas N lineas.

```powershell
.\tools\vps_logs.ps1                       # follow (Ctrl+C para salir)
.\tools\vps_logs.ps1 -Tail 200             # ultimas 200 lineas y salir
.\tools\vps_logs.ps1 -Service buysell365_admin
.\tools\vps_logs.ps1 -Errors -Tail 50      # solo errores
```

### `vps_status.ps1`
Snapshot rapido: servicios, procesos, git, disco, RAM, ultimo log.

```powershell
.\tools\vps_status.ps1
```

### `vps_restart.ps1`
Reinicia el bot sin pull.

```powershell
.\tools\vps_restart.ps1            # solo bot principal
.\tools\vps_restart.ps1 -All       # bot + panel admin
```

## Workflow tipico

```
1. Editar archivos en BuySell365/app/
2. .\deploy.ps1 "fix: lo que sea"
3. .\tools\vps_logs.ps1 -Tail 50    (verificar que arranco OK)
```

## Si algo falla

- SSH no funciona → revisar que la key publica esta en el VPS
  (`cat ~/.ssh/authorized_keys` en VPS debe incluir nuestra `.pub`)
- Push falla → revisar credenciales GitHub (Git Credential Manager)
- Restart falla → `vps_logs.ps1 -Errors -Tail 50` y arreglar
- VPS no responde → comprobar IP via panel InterServer
