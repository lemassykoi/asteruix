# UX Improvement Plan: Asterisk WebUI

This document outlines a comprehensive strategy to transform the Asterisk WebUI from a "functionality-first" tool into a modern, intuitive, and highly efficient user experience.

---

## 1. Visual Identity & Foundation

### Modernize the Design System
*   **Refined Color Palette**: Move away from standard high-contrast colors to a more balanced, professional palette (e.g., Slate, Indigo, and Emerald for success).
*   **Typography**: Implement a modern sans-serif stack (e.g., Inter, Geist, or System Sans) with better hierarchy and readability.
*   **Dark Mode Support**: Add a first-class dark mode using CSS variables for high-comfort usage in server room environments.
*   **Consistent Componentry**: Standardize shadows, border radii (4px-8px), and spacing (8px grid) across all elements.

### Layout Restructuring
*   **Collapsible Sidebar**: Replace the cluttered top navigation with a collapsible sidebar. This allows for better organization of the many features (Extensions, Trunks, Inbound, etc.) and leaves more horizontal space for tables and forms.
*   **Breadcrumbs**: Implement breadcrumb navigation to help users maintain context, especially in multi-step processes like Voicemail management or Inbound Route configuration.
*   **Responsive Refinement**: Optimize for tablets and mobile devices for "on-the-go" emergency changes.

---

## 2. Interaction Design & Feedback

### Real-time & Asynchronous Updates
*   **Enhanced Dashboard**: Transition from manual 3s polling to a more robust real-time system (WebSockets or Server-Sent Events).
*   **Seamless Transitions**: Use HTMX or similar for partial page loads, reducing full-page refreshes and making the UI feel like a single-page application (SPA).
*   **Loading States**: Introduce skeleton screens and progress bars for long-running tasks like audio conversion or backup restoration.

### Toast Notifications & Contextual Feedback
*   **Toasts**: Replace standard flash messages with non-intrusive toast notifications for success/error feedback.
*   **Inline Validation**: Real-time form validation (e.g., checking if an extension number is already in use) before the user hits "Save".
*   **Empty States**: Design meaningful empty states with "Call to Action" buttons (e.g., "You have no extensions yet. [Create One]").

---

## 3. Feature-Specific UX Enhancements

### Audio Management (MoH, Announcements, Voicemail)
*   **Inline Audio Player**: Replace "Play in new tab" with a custom, sleek inline audio player.
*   **Waveform Visualization**: Show waveforms for tracks to help identify silence or clipping.
*   **Drag-and-Drop Uploads**: Implement a modern file upload area with progress tracking and multi-file support.

### Inbound Routing & Dialplan
*   **Interactive Flow Editor**: Transform the read-only "Flow Steps" into an interactive drag-and-drop builder for inbound routes.
*   **Visual Logic Highlighting**: In the Dialplan Visualization, highlight the active path or allow users to simulate a call flow.
*   **Time Group Visualizer**: Add a calendar-style view for Time Groups to easily spot overlaps or gaps in business hours.

### Table & Data Management
*   **Advanced Tables**: Implement sorting, global search, and filtering for all list views (Extensions, Spam List, etc.).
*   **Bulk Actions**: Allow users to select multiple items (e.g., spam numbers or voicemails) to delete or move them in one click.

---

## 4. Immediate "Quick Wins" (Phase 1)

1.  **Sidebar Transition**: Move navigation to the left.
2.  **Inline Audio**: Add a simple `<audio>` tag wrapper instead of a raw link.
3.  **Enhanced Flash Messages**: Style them as floating toasts.
4.  **Dashboard Polish**: Use better icons and grid layouts for system stats.
5.  **Standardized Forms**: Align all forms to a single-column, max-width layout with clear section headers.

---

## 5. Accessibility & Performance

*   **A11y Compliance**: Ensure WCAG 2.1 compliance (color contrast, ARIA labels, keyboard focus states).
*   **Performance Optimization**: Minimize CSS/JS payloads and optimize images/icons.
*   **Searchability**: Add a global "Command Palette" (Ctrl+K) for quick navigation to any section of the PBX.
