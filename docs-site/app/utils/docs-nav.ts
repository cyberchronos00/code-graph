export interface DocLink {
  slug: string
  title: string
}

export interface DocGroup {
  title: string
  items: DocLink[]
}

/** Sidebar order. Pages themselves stay in the repo `docs/` tree. */
export const docGroups: DocGroup[] = [
  {
    title: 'Start',
    items: [
      { slug: 'install', title: 'Install' },
      { slug: 'cli', title: 'CLI' },
      { slug: 'configuration', title: 'Configuration' },
      { slug: 'mcp', title: 'MCP' }
    ]
  },
  {
    title: 'Graph',
    items: [
      { slug: 'architecture', title: 'Architecture' },
      { slug: 'schema', title: 'Schema' },
      { slug: 'completeness', title: 'Completeness' },
      { slug: 'validation', title: 'Validation' },
      { slug: 'limitations', title: 'Limitations' },
      { slug: 'parity', title: 'Parity' },
      { slug: 'value-facts', title: 'Value facts' },
      { slug: 'plans', title: 'Plans' },
      { slug: 'viz', title: 'Visual view' },
      { slug: 'generated', title: 'Generated files' }
    ]
  },
  {
    title: 'Languages',
    items: [
      { slug: 'python', title: 'Python' },
      { slug: 'php', title: 'PHP' },
      { slug: 'kotlin', title: 'Kotlin' },
      { slug: 'swift', title: 'Swift' },
      { slug: 'native', title: 'Rust, C and C++' },
      { slug: 'ts-frameworks', title: 'TypeScript frameworks' }
    ]
  },
  {
    title: 'Boundaries',
    items: [
      { slug: 'platforms', title: 'Platforms' },
      { slug: 'bridges', title: 'Bridges' },
      { slug: 'protocols', title: 'Protocols' },
      { slug: 'external', title: 'External systems' },
      { slug: 'ai-tools', title: 'AI tools' },
      { slug: 'channels-and-tests', title: 'Channels and tests' }
    ]
  },
  {
    title: 'Reference',
    items: [
      { slug: 'mcp/sample_outputs', title: 'MCP sample outputs' }
    ]
  }
]

export const docRoutes = docGroups.flatMap(group =>
  group.items.map(item => `/docs/${item.slug}`)
)
