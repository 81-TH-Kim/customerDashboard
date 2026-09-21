# RPA 제휴현황 대시보드 - 자동 수집 작업 스케줄러 등록/갱신 (Windows)
#
#   PowerShell 에서:  powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1
#
# 매일 07:30, 09:30 두 번 run_collect.bat 실행 (RPA 메일이 08시 이후 도착하는 경우 대비).
# 이미 등록돼 있으면 덮어쓴다. run_collect.bat 이 수집(data\live) + 발행(GitHub push)까지 처리한다.
#
# 주의: 이 파일은 UTF-8 (BOM) 로 저장돼야 한글 작업 이름이 깨지지 않는다.
# 주의: 이 스크립트는 작업을 InteractiveToken(로그인 상태에서만 실행)으로 등록한다.
#       로그인 없이도 실행되게 하려면 재등록 후 아래를 한 번 더 실행할 것(README 2절 참고):
#         schtasks /Change /TN "ABL_RPA_제휴현황_수집" /RU "%USERNAME%" /RP *

$ErrorActionPreference = "Stop"
$TaskName = "ABL_RPA_제휴현황_수집"
$Root = Split-Path -Parent $PSScriptRoot
$Bat  = Join-Path $Root "run_collect.bat"
if (-not (Test-Path $Bat)) { throw "run_collect.bat 를 찾을 수 없습니다: $Bat" }

$xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>RPA 제휴현황 메일 수집 (매일 07:30 / 09:30). data\live 갱신. 공개 반영은 publish.bat 수동.</Description>
  </RegistrationInfo>
  <Principals>
    <Principal id="Author">
      <UserId>$([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)</UserId>
      <LogonType>InteractiveToken</LogonType>
    </Principal>
  </Principals>
  <Settings>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT15M</ExecutionTimeLimit>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <StartWhenAvailable>true</StartWhenAvailable>
    <UseUnifiedSchedulingEngine>true</UseUnifiedSchedulingEngine>
  </Settings>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>2026-09-09T07:30:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
    </CalendarTrigger>
    <CalendarTrigger>
      <StartBoundary>2026-09-09T09:30:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Actions Context="Author">
    <Exec>
      <Command>$Bat</Command>
      <WorkingDirectory>$Root</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@

$tmp = Join-Path $env:TEMP "abl_rpa_task.xml"
[System.IO.File]::WriteAllText($tmp, $xml, [System.Text.Encoding]::Unicode)
schtasks /Create /TN $TaskName /XML $tmp /F
Remove-Item $tmp -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "등록 완료. 트리거:"
(Get-ScheduledTask -TaskName $TaskName).Triggers | ForEach-Object { "  " + $_.StartBoundary }
