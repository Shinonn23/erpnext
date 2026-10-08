
export const getCompanyCurrency = (company: string) => {
    // @ts-expect-error - Locals is synced
    return locals[':Company']?.[company]?.['default_currency']
}

export const getCompany = (company: string) => {
    // @ts-expect-error - Locals is synced
    return locals?.[':Company']?.[company]
}
