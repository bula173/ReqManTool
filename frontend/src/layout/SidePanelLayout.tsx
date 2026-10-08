import type { ReactNode } from 'react';
import { useAppActions } from '../actions/context';
import { Sidebar } from '../components/Sidebar';
import { PanelEdge, PanelRail, SidePanelOverlay } from './SidePanelParts';
import { usePanelWidth, type PanelWidth } from './sidePanels';

const LEFT_LABEL = 'navigation panel';
const LEFT_ACTION = 'view.toggleLeftPanel';
const RIGHT_LABEL = 'requirement detail panel';
const RIGHT_ACTION = 'view.toggleDetailPanel';

/**
 * The content of the app shell's navbar: the project/module tree (`Sidebar`)
 * with a resizable border, or its rail when collapsed. On a narrow screen the
 * navbar always shows the rail and the open panel is an overlay. The navbar's
 * width (`AppLayout`) follows `width` and the open state.
 */
export function LeftSidePanel({ width }: { width: PanelWidth }) {
  const { state, run } = useAppActions();
  const toggle = () => run(LEFT_ACTION);

  if (state.leftPanelOpen && !state.narrowLayout) {
    return (
      <div className="reqman-side-panel" data-side="left">
        <Sidebar />
        <PanelEdge side="left" width={width} label={LEFT_LABEL} toggleActionId={LEFT_ACTION} onCollapse={toggle} />
      </div>
    );
  }

  return (
    <>
      <PanelRail side="left" label={LEFT_LABEL} title="Navigation" toggleActionId={LEFT_ACTION} onExpand={toggle} />
      {state.leftPanelOpen && (
        <SidePanelOverlay side="left" width={width} label={LEFT_LABEL} toggleActionId={LEFT_ACTION} onClose={toggle}>
          <div className="reqman-side-panel" data-side="left">
            <Sidebar />
          </div>
        </SidePanelOverlay>
      )}
    </>
  );
}

/**
 * The module page's right panel (`children`: the requirement detail panel):
 * inline with a resizable border next to the table, a rail when collapsed, or
 * an overlay on a narrow screen. The table takes whatever width is left.
 */
export function RightSidePanel({ children }: { children: ReactNode }) {
  const { state, run } = useAppActions();
  const width = usePanelWidth('right');
  const toggle = () => run(RIGHT_ACTION);

  if (state.detailPanelOpen && !state.narrowLayout) {
    return (
      <div className="reqman-side-panel" data-side="right" style={{ width: width.width }}>
        {children}
        <PanelEdge
          side="right"
          width={width}
          label={RIGHT_LABEL}
          toggleActionId={RIGHT_ACTION}
          onCollapse={toggle}
          inGap
        />
      </div>
    );
  }

  return (
    <>
      <PanelRail
        side="right"
        label={RIGHT_LABEL}
        title="Details"
        toggleActionId={RIGHT_ACTION}
        onExpand={toggle}
        bordered
      />
      {state.detailPanelOpen && (
        <SidePanelOverlay side="right" width={width} label={RIGHT_LABEL} toggleActionId={RIGHT_ACTION} onClose={toggle}>
          <div className="reqman-side-panel" data-side="right">
            {children}
          </div>
        </SidePanelOverlay>
      )}
    </>
  );
}
