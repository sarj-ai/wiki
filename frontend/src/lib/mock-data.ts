import type { Page, Workspace, User, PagePermission } from "@/types";

export const mockUser: User = {
  id: "u-1",
  email: "ali@sarj.ai",
  displayName: "Ali Alhashemi",
  avatarUrl: null,
};

export const mockWorkspaces: Workspace[] = [
  {
    id: "ws-1",
    name: "Engineering",
    googleGroupEmail: "engineering@sarj.ai",
  },
  {
    id: "ws-2",
    name: "Marketing",
    googleGroupEmail: "marketing@sarj.ai",
  },
];

export const mockEngineeringPages: Page[] = [
  {
    id: "p-1",
    parentId: null,
    googleDriveId: "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms",
    title: "Culture",
    type: "section",
    icon: "\u{1F3E0}",
    isPrivate: false,
    workspaceId: "ws-1",
    sortOrder: 0,
    children: [
      {
        id: "p-2",
        parentId: "p-1",
        googleDriveId: "1kP3s9qHmRvYnTz2XcVbNmLwDfEgHiJkLmNoPqRsTuV",
        title: "How We Work",
        type: "page",
        icon: "\u{1F4CB}",
        isPrivate: false,
        workspaceId: "ws-1",
        sortOrder: 0,
        children: [],
      },
      {
        id: "p-3",
        parentId: "p-1",
        googleDriveId: "1aB2cD3eF4gH5iJ6kL7mN8oP9qR0sT1uV2wX3yZ4aB",
        title: "Onboarding Guide",
        type: "page",
        icon: "\u{1F44B}",
        isPrivate: false,
        workspaceId: "ws-1",
        sortOrder: 1,
        children: [],
      },
    ],
  },
  {
    id: "p-4",
    parentId: null,
    googleDriveId: "1cD4eF5gH6iJ7kL8mN9oP0qR1sT2uV3wX4yZ5aB6cD",
    title: "API Guidelines",
    type: "page",
    icon: "\u{1F4E1}",
    isPrivate: false,
    workspaceId: "ws-1",
    sortOrder: 1,
    children: [],
  },
  {
    id: "p-5",
    parentId: null,
    googleDriveId: "1eF6gH7iJ8kL9mN0oP1qR2sT3uV4wX5yZ6aB7cD8eF",
    title: "Projects",
    type: "section",
    icon: "\u{1F680}",
    isPrivate: false,
    workspaceId: "ws-1",
    sortOrder: 2,
    children: [
      {
        id: "p-6",
        parentId: "p-5",
        googleDriveId: "1gH8iJ9kL0mN1oP2qR3sT4uV5wX6yZ7aB8cD9eF0gH",
        title: "Project Alpha",
        type: "page",
        icon: "\u{1F4C2}",
        isPrivate: false,
        workspaceId: "ws-1",
        sortOrder: 0,
        children: [
          {
            id: "p-7",
            parentId: "p-6",
            googleDriveId: "1iJ0kL1mN2oP3qR4sT5uV6wX7yZ8aB9cD0eF1gH2iJ",
            title: "Technical Spec",
            type: "page",
            icon: "\u{1F4DD}",
            isPrivate: false,
            workspaceId: "ws-1",
            sortOrder: 0,
            children: [],
          },
          {
            id: "p-8",
            parentId: "p-6",
            googleDriveId: "1kL2mN3oP4qR5sT6uV7wX8yZ9aB0cD1eF2gH3iJ4kL",
            title: "Meeting Notes",
            type: "page",
            icon: "\u{1F5D2}\uFE0F",
            isPrivate: false,
            workspaceId: "ws-1",
            sortOrder: 1,
            children: [],
          },
        ],
      },
      {
        id: "p-9",
        parentId: "p-5",
        googleDriveId: "1mN4oP5qR6sT7uV8wX9yZ0aB1cD2eF3gH4iJ5kL6mN",
        title: "Project Beta",
        type: "page",
        icon: "\u{1F4C2}",
        isPrivate: false,
        workspaceId: "ws-1",
        sortOrder: 1,
        children: [],
      },
    ],
  },
  {
    id: "p-10",
    parentId: null,
    googleDriveId: "1oP6qR7sT8uV9wX0yZ1aB2cD3eF4gH5iJ6kL7mN8oP",
    title: "Architecture Decisions",
    type: "page",
    icon: "\u{1F3D7}\uFE0F",
    isPrivate: false,
    workspaceId: "ws-1",
    sortOrder: 3,
    children: [],
  },
];

export const mockMarketingPages: Page[] = [
  {
    id: "p-20",
    parentId: null,
    googleDriveId: "1qR8sT9uV0wX1yZ2aB3cD4eF5gH6iJ7kL8mN9oP0qR",
    title: "Brand Guidelines",
    type: "page",
    icon: "\u{1F3A8}",
    isPrivate: false,
    workspaceId: "ws-2",
    sortOrder: 0,
    children: [],
  },
  {
    id: "p-21",
    parentId: null,
    googleDriveId: "1sT0uV1wX2yZ3aB4cD5eF6gH7iJ8kL9mN0oP1qR2sT",
    title: "Content",
    type: "section",
    icon: "\u{270D}\uFE0F",
    isPrivate: false,
    workspaceId: "ws-2",
    sortOrder: 1,
    children: [
      {
        id: "p-22",
        parentId: "p-21",
        googleDriveId: "1uV2wX3yZ4aB5cD6eF7gH8iJ9kL0mN1oP2qR3sT4uV",
        title: "Blog Strategy",
        type: "page",
        icon: "\u{1F4DD}",
        isPrivate: false,
        workspaceId: "ws-2",
        sortOrder: 0,
        children: [],
      },
      {
        id: "p-23",
        parentId: "p-21",
        googleDriveId: "1wX4yZ5aB6cD7eF8gH9iJ0kL1mN2oP3qR4sT5uV6wX",
        title: "Social Media Calendar",
        type: "page",
        icon: "\u{1F4C5}",
        isPrivate: false,
        workspaceId: "ws-2",
        sortOrder: 1,
        children: [],
      },
    ],
  },
  {
    id: "p-24",
    parentId: null,
    googleDriveId: "1yZ6aB7cD8eF9gH0iJ1kL2mN3oP4qR5sT6uV7wX8yZ",
    title: "Campaign Tracker",
    type: "page",
    icon: "\u{1F4CA}",
    isPrivate: false,
    workspaceId: "ws-2",
    sortOrder: 2,
    children: [],
  },
];

export const mockPrivatePages: Page[] = [
  {
    id: "p-30",
    parentId: null,
    googleDriveId: "1aB8cD9eF0gH1iJ2kL3mN4oP5qR6sT7uV8wX9yZ0aB",
    title: "My Draft Ideas",
    type: "page",
    icon: "\u{1F4A1}",
    isPrivate: true,
    workspaceId: null,
    sortOrder: 0,
    children: [],
  },
  {
    id: "p-31",
    parentId: null,
    googleDriveId: "1cD0eF1gH2iJ3kL4mN5oP6qR7sT8uV9wX0yZ1aB2cD",
    title: "Personal Notes",
    type: "page",
    icon: "\u{1F512}",
    isPrivate: true,
    workspaceId: null,
    sortOrder: 1,
    children: [],
  },
];

export function getPagesForWorkspace(workspaceId: string): Page[] {
  switch (workspaceId) {
    case "ws-1":
      return mockEngineeringPages;
    case "ws-2":
      return mockMarketingPages;
    default:
      return [];
  }
}

/** All mock pages combined (all workspaces + private) */
export const allMockPages: Page[] = [
  ...mockEngineeringPages,
  ...mockMarketingPages,
  ...mockPrivatePages,
];

/** Recursively search for a page by ID in a page tree */
function searchPages(pages: Page[], id: string): Page | null {
  for (const page of pages) {
    if (page.id === id) return page;
    if (page.children.length > 0) {
      const found = searchPages(page.children, id);
      if (found) return found;
    }
  }
  return null;
}

/** Find a page by ID from all mock data */
export function findPageById(id: string): Page | null {
  return searchPages(allMockPages, id);
}

/** Mock workspace members for sharing suggestions */
export const mockWorkspaceMembers: User[] = [
  {
    id: "u-1",
    email: "ali@sarj.ai",
    displayName: "Ali Alhashemi",
    avatarUrl: null,
  },
  {
    id: "u-2",
    email: "sara@sarj.ai",
    displayName: "Sara Khalid",
    avatarUrl: null,
  },
  {
    id: "u-3",
    email: "omar@sarj.ai",
    displayName: "Omar Nasser",
    avatarUrl: null,
  },
];

/** Mock permissions for pages */
export const mockPagePermissions: PagePermission[] = [
  // Engineering "How We Work" page — shared with Engineering workspace + one extra user
  {
    id: "perm-1",
    pageId: "p-2",
    userId: null,
    workspaceId: "ws-1",
    permission: "edit",
    displayName: "Engineering",
    email: "engineering@sarj.ai",
  },
  {
    id: "perm-2",
    pageId: "p-2",
    userId: "u-2",
    workspaceId: null,
    permission: "view",
    displayName: "Sara Khalid",
    email: "sara@sarj.ai",
  },
  // API Guidelines page — shared with Engineering workspace
  {
    id: "perm-3",
    pageId: "p-4",
    userId: null,
    workspaceId: "ws-1",
    permission: "edit",
    displayName: "Engineering",
    email: "engineering@sarj.ai",
  },
  // Brand Guidelines — shared with Marketing workspace + one extra user
  {
    id: "perm-4",
    pageId: "p-20",
    userId: null,
    workspaceId: "ws-2",
    permission: "edit",
    displayName: "Marketing",
    email: "marketing@sarj.ai",
  },
  {
    id: "perm-5",
    pageId: "p-20",
    userId: "u-3",
    workspaceId: null,
    permission: "view",
    displayName: "Omar Nasser",
    email: "omar@sarj.ai",
  },
];

/** Get permissions for a specific page */
export function getPermissionsForPage(pageId: string): PagePermission[] {
  return mockPagePermissions.filter((p) => p.pageId === pageId);
}
