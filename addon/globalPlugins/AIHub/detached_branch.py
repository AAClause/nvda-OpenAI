"""Soft-detach archived conversation branches for regenerate + restore."""

from __future__ import annotations

import copy

import addonHandler

from .usage_ledger import ensure_block_uid

addonHandler.initTranslation()


def find_block_by_uid(first_block, block_uid: str):
	if not block_uid or first_block is None:
		return None
	block = first_block
	while block:
		if getattr(block, "uid", None) == block_uid:
			return block
		block = block.next
	return None


def count_chain_blocks(first_block) -> int:
	count = 0
	block = first_block
	while block:
		count += 1
		block = block.next
	return count


def build_anchor_snapshot(block) -> dict:
	usage = getattr(block, "usage", None)
	timing = getattr(block, "timing", None)
	return {
		"responseText": block.responseText or "",
		"reasoningText": getattr(block, "reasoningText", "") or "",
		"usage": copy.deepcopy(usage) if isinstance(usage, dict) else None,
		"timing": copy.deepcopy(timing) if isinstance(timing, dict) else None,
		"responseTerminated": bool(getattr(block, "responseTerminated", False)),
	}


def apply_anchor_snapshot(block, snapshot: dict) -> None:
	if not isinstance(snapshot, dict):
		return
	block.responseText = snapshot.get("responseText") or ""
	block.reasoningText = snapshot.get("reasoningText") or ""
	usage = snapshot.get("usage")
	block.usage = copy.deepcopy(usage) if isinstance(usage, dict) else None
	timing = snapshot.get("timing")
	block.timing = copy.deepcopy(timing) if isinstance(timing, dict) else {}
	block.responseTerminated = bool(snapshot.get("responseTerminated", True))
	block.displayHeader = False
	block.lastLen = len(block.responseText or "")
	block.lastReasoningLen = len(block.reasoningText or "")


def detach_tail_for_regenerate(page, anchor_block) -> dict:
	"""Archive the anchor assistant turn and following blocks; unlink tail from the active chain."""
	ensure_block_uid(anchor_block)
	tail_first = anchor_block.next
	tail_last = None
	if tail_first is not None:
		tail_last = tail_first
		while tail_last.next is not None:
			tail_last = tail_last.next
		anchor_block.next = None
		tail_first.previous = None
		page.lastBlock = anchor_block

	detached = {
		"anchorBlockId": anchor_block.uid,
		"anchorSnapshot": build_anchor_snapshot(anchor_block),
		"tailFirstBlock": tail_first,
		"tailLastBlock": tail_last,
	}
	page.detachedBranch = detached
	return detached


def clear_detached_branch(page) -> None:
	page.detachedBranch = None


def has_detached_branch(page) -> bool:
	branch = getattr(page, "detachedBranch", None)
	return isinstance(branch, dict) and bool(branch.get("anchorBlockId"))


def is_detached_branch_anchor(page, block) -> bool:
	branch = getattr(page, "detachedBranch", None)
	if not isinstance(branch, dict):
		return False
	anchor_uid = branch.get("anchorBlockId")
	return bool(anchor_uid) and getattr(block, "uid", None) == anchor_uid


def assistant_label_for_block(page, block, *, default_label: str) -> str:
	"""History assistant prefix; marks the anchor block when a branch is archived."""
	if not is_detached_branch_anchor(page, block):
		return default_label
	branch = page.detachedBranch
	tail_count, _had_response = detached_branch_summary(branch)
	if tail_count:
		# Translators: Assistant response label in history when regenerate archived later messages.
		return _("Assistant [archived branch, %d later messages]:") % tail_count + " "
	# Translators: Assistant response label in history when regenerate archived the prior response.
	return _("Assistant [archived branch]:") + " "


def detached_tail_count(branch) -> int:
	if not isinstance(branch, dict):
		return 0
	return count_chain_blocks(branch.get("tailFirstBlock"))


def detached_branch_summary(branch) -> tuple[int, bool]:
	"""Return ``(tail_message_count, had_anchor_response)`` for user-facing status."""
	if not isinstance(branch, dict):
		return 0, False
	snapshot = branch.get("anchorSnapshot") if isinstance(branch.get("anchorSnapshot"), dict) else {}
	had_response = bool(
		(snapshot.get("responseText") or "").strip()
		or (snapshot.get("reasoningText") or "").strip()
	)
	return detached_tail_count(branch), had_response


def serialize_detached_branch(branch, block_to_dict) -> dict | None:
	"""Serialize in-memory detached branch using ``block_to_dict`` for tail blocks."""
	if not isinstance(branch, dict) or not branch.get("anchorBlockId"):
		return None
	tail_blocks = []
	tail = branch.get("tailFirstBlock")
	while tail is not None:
		tail_blocks.append(block_to_dict(tail))
		tail = tail.next
	payload = {
		"anchorBlockId": branch["anchorBlockId"],
		"anchorSnapshot": copy.deepcopy(branch.get("anchorSnapshot") or {}),
		"blocks": tail_blocks,
	}
	return payload


def deserialize_detached_branch(data, dict_to_block, *, conv_id: str = "", block_idx_offset: int = 0):
	"""Rebuild in-memory detached branch from saved JSON."""
	if not isinstance(data, dict) or not data.get("anchorBlockId"):
		return None
	tail_blocks_data = data.get("blocks")
	if not isinstance(tail_blocks_data, list):
		tail_blocks_data = []
	blocks = []
	for i, bd in enumerate(tail_blocks_data):
		if not isinstance(bd, dict):
			continue
		blocks.append(dict_to_block(bd, conv_id=conv_id, block_idx=block_idx_offset + i))
	tail_first = None
	tail_last = None
	prev = None
	for b in blocks:
		b.displayHeader = False
		b.responseTerminated = True
		if prev is not None:
			prev.next = b
			b.previous = prev
		else:
			tail_first = b
		prev = b
		tail_last = b
	return {
		"anchorBlockId": data["anchorBlockId"],
		"anchorSnapshot": copy.deepcopy(data.get("anchorSnapshot") or {}),
		"tailFirstBlock": tail_first,
		"tailLastBlock": tail_last,
	}


def restore_detached_branch(page, first_block, last_block):
	"""Restore archived branch onto the active chain. Returns ``(anchor, new_last)`` or ``(None, None)``."""
	branch = getattr(page, "detachedBranch", None)
	if not isinstance(branch, dict):
		return None, None
	anchor_uid = branch.get("anchorBlockId")
	anchor = find_block_by_uid(first_block, anchor_uid)
	if anchor is None:
		clear_detached_branch(page)
		return None, None

	# Drop the current suffix (regenerated response and any turns added since).
	anchor.next = None

	snapshot = branch.get("anchorSnapshot")
	apply_anchor_snapshot(anchor, snapshot if isinstance(snapshot, dict) else {})

	tail_first = branch.get("tailFirstBlock")
	tail_last = branch.get("tailLastBlock")
	if tail_first is not None:
		anchor.next = tail_first
		tail_first.previous = anchor
		new_last = tail_last or tail_first
	else:
		new_last = anchor

	clear_detached_branch(page)
	return anchor, new_last
