{{- define "trial-risk.labels" -}}
app.kubernetes.io/part-of: trial-risk
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{- define "trial-risk.image" -}}
{{ .repository }}:{{ .tag }}
{{- end -}}

{{/* envFrom shared by every pod that needs the platform settings. */}}
{{- define "trial-risk.envFrom" -}}
- configMapRef:
    name: {{ .Values.configMapName }}
- secretRef:
    name: {{ .Values.secrets.name }}
    optional: true
{{- end -}}

{{/* CSI volume + mount that make the Secrets Store driver sync the Secret. */}}
{{- define "trial-risk.csiVolume" -}}
- name: secrets-store
  csi:
    driver: secrets-store.csi.k8s.io
    readOnly: true
    volumeAttributes:
      secretProviderClass: {{ .Release.Name }}-kv
{{- end -}}
{{- define "trial-risk.csiMount" -}}
- name: secrets-store
  mountPath: /mnt/secrets-store
  readOnly: true
{{- end -}}
