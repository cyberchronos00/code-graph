export function submitLabel() {
  return t("acme_orders.submit")
}

export function cancelLabel() {
  return $t('acme_orders.cancel')
}

export function dynamicLabel(name) {
  return t(`acme_orders.${name}`)
}

export function indirect(key) {
  return t(key)
}
