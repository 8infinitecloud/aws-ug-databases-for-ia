#!/usr/bin/env bash
# =============================================================================
# core.sh — ejecutar SQL en el PostgreSQL del core, sin abrir puertos
# =============================================================================
# Se sourcea despues de tf.sh:
#
#   source "${REPO_ROOT}/scripts/lib/tf.sh"
#   source "${REPO_ROOT}/scripts/lib/core.sh"
#   core_sql "SELECT count(*) FROM pg_replication_slots"
#
# La base esta en una instancia sin puertos abiertos a internet. Esto le pide a
# la propia instancia, por SSM Run Command, que ejecute la consulta con psql y
# devuelve lo que imprime. Sin tunel, sin contrasena en tu maquina.
# =============================================================================

# core_sql <sql>  — imprime el resultado (formato -tA: sin cabecera ni bordes).
core_sql() {
  local sql="$1"
  local region="${AWS_REGION:-us-east-1}"
  local instancia
  instancia=$(tf_output payments db_instance_id)
  [ -n "${instancia}" ] || { echo "core_sql: infra/payments no esta desplegado" >&2; return 1; }

  # jq construye el JSON de parametros: asi las comillas del SQL no rompen nada.
  local params
  params=$(jq -n --arg sql "${sql}" \
    '{commands: ["sudo -u postgres psql -d acquiring -tA -v ON_ERROR_STOP=1 -c " + ($sql | @sh)]}')

  local cmd
  cmd=$(aws ssm send-command --region "${region}" --instance-ids "${instancia}" \
          --document-name AWS-RunShellScript --parameters "${params}" \
          --query Command.CommandId --output text) || return 1

  local estado="Pending"
  for _ in $(seq 1 20); do
    sleep 2
    estado=$(aws ssm get-command-invocation --region "${region}" \
               --command-id "${cmd}" --instance-id "${instancia}" \
               --query Status --output text 2>/dev/null || echo Pending)
    case "${estado}" in Success|Failed|Cancelled|TimedOut) break ;; esac
  done

  aws ssm get-command-invocation --region "${region}" --command-id "${cmd}" \
      --instance-id "${instancia}" --query StandardOutputContent --output text
  [ "${estado}" = "Success" ]
}

# core_drop_inactive_slots — borra los slots de replicacion que nadie usa.
#
# Un slot sin consumidor retiene WAL hasta llenar el disco del core. DMS no lo
# borra al destruir su tarea: si nadie lo hace, el CDC del lab se convierte en
# una bomba de relojeria en la base de produccion. Solo los INACTIVOS: uno
# activo tiene una tarea leyendo, y borrarlo la romperia.
core_drop_inactive_slots() {
  core_sql "SELECT slot_name, pg_drop_replication_slot(slot_name) FROM pg_replication_slots WHERE NOT active"
}
