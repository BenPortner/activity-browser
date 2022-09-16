# -*- coding: utf-8 -*-
from functools import partial
from numbers import Real
from typing import Callable, Dict, List, Optional, Set, Tuple, Union

import numpy as np
from bw2calc import LCA


class GTNode:
    def __init__(
        self,
        index: int,
        key: Optional[tuple] = None,
        amount: Optional[Real] = None,
        cum: Optional[Real] = None,
        ind: Optional[Real] = None,
    ):
        self.index = (
            index  # matrix index of the technosphere or biosphere activity
        )
        self.key = key  # key of the technosphere or biosphere activity
        self.amount = amount  # total amount of main product in LCI
        self.cum = cum  # total (direct+indirect) impact
        self.ind = ind  # individual (direct) impact

    def to_dict(self) -> Dict:
        return {"amount": self.amount, "cum": self.cum, "ind": self.ind}


class GTTechnosphereNode(GTNode):
    def __init__(
        self,
        index: int,
        lca: LCA,
        cb: np.array,
        supply: np.array,
        key: Optional[tuple] = None,
    ):
        super().__init__(index, key)
        amount = self._lci_amount(supply)
        self.amount = amount
        self.cum = self._unit_score(lca, cb) * amount
        self.ind = 0

    def _lci_amount(self, supply: np.array) -> Real:
        return supply[self.index]

    def _unit_score(self, lca: LCA, cb: np.array) -> float:
        """Compute LCA score for one unit of a given technosphere activity"""
        demand = np.zeros(lca.demand_array.shape)
        demand[self.index] = 1
        return float((cb * lca.solver(demand)).sum())

    def scaled_score(self, lca: LCA, cb: np.array, factor: Real) -> Real:
        return self._unit_score(lca, cb) * factor


class GTBiosphereNode(GTNode):
    def __init__(
        self,
        index: int,
        lca: LCA,
        fu_amount: Real,
        key: Optional[tuple] = None,
    ):
        super().__init__(index, key)
        amount = self._lci_amount(lca, fu_amount)
        unit_score = self._unit_score(lca)
        self.amount = amount
        self.cum = unit_score * amount
        self.ind = unit_score * amount

    def _lci_amount(self, lca: LCA, fu_amount: Real) -> Real:
        return lca.inventory.sum(axis=1)[self.index].sum() / fu_amount

    def _unit_score(self, lca: LCA) -> Real:
        """Compute LCA score for one unit of a given biosphere activity"""
        return lca.characterization_matrix[self.index, self.index]

    def scaled_score(self, lca: LCA, factor: Real) -> Real:
        return self._unit_score(lca) * factor


class GTNodeSet:
    def __init__(self):
        self.nodes = set()

    def __contains__(self, node: GTNode) -> bool:
        return node in self.nodes

    def get_by_index(
        self, index: int
    ) -> Optional[Union[GTBiosphereNode, GTTechnosphereNode]]:
        for node in self.nodes:
            if node.index == index:
                return node
        return None

    def add(self, node: GTNode) -> None:
        self.nodes.add(node)

    def add_node_keys(self, rev_act: Dict, rev_bio: Dict) -> None:
        for n in self.nodes:
            if n.index == -1:
                n.key = -1
            else:
                rev_dict = (
                    rev_act if isinstance(n, GTTechnosphereNode) else rev_bio
                )
                n.key = rev_dict[n.index]

    def to_dict(self, use_keys: bool = True) -> Dict:
        if use_keys:
            return {n.key: n.to_dict() for n in self.nodes}
        else:
            return {n.index: n.to_dict() for n in self.nodes}


class GTEdge:
    def __init__(
        self,
        to_node: GTNode,
        from_node: GTNode,
        amount: Real,
        exc_amount: Real,
        impact: Real,
    ):
        self.to_node = to_node
        self.from_node = from_node
        self.amount = amount
        self.exc_amount = exc_amount
        self.impact = impact

    def to_dict(self, use_keys: bool = True) -> Dict:
        if use_keys:
            return {
                "to": self.to_node.key,
                "from": self.from_node.key,
                "amount": self.amount,
                "exc_amount": self.exc_amount,
                "impact": self.impact,
            }
        else:
            return {
                "to": self.to_node.index,
                "from": self.from_node.index,
                "amount": self.amount,
                "exc_amount": self.exc_amount,
                "impact": self.impact,
            }


class GTEdgeList:
    def __init__(self, edges: Optional[List[GTEdge]] = None):
        self.edges = edges

    def __contains__(self, edge: GTEdge) -> bool:
        return edge in self.edges

    def get(
        self,
        to_node: GTNode,
        from_node: GTNode,
    ) -> Optional[Union[GTBiosphereNode, GTTechnosphereNode]]:
        for edge in self.edges:
            if edge.to_node == to_node and edge.from_node == from_node:
                return edge
        return None

    def append(self, edge: GTEdge) -> None:
        self.edges.append(edge)

    def to_list(self, use_keys: bool = True) -> List[Dict]:
        return [e.to_dict(use_keys) for e in self.edges]

    def get_unique_nodes(self) -> Set[GTNode]:
        return set(
            [e.from_node for e in self.edges] + [e.to_node for e in self.edges]
        )


class GraphTraversal:
    """
    Traverse a supply chain, following paths of greatest impact.

    This implementation uses a queue of datasets to assess. As the supply chain is traversed, datasets inputs are added to a list sorted by LCA score. Each activity in the sorted list is assessed, and added to the supply chain graph, as long as its impact is above a certain threshold, and the maximum number of calculations has not been exceeded.

    Because the next dataset assessed is chosen by its impact, not its position in the graph, this is neither a breadth-first nor a depth-first search, but rather "importance-first".

    This class is written in a functional style - no variables are stored in *self*, only methods.

    Should be used by calling the ``calculate`` method.

    .. warning:: Graph traversal with multioutput processes only works when other inputs are substituted (see `Multioutput processes in LCA <http://chris.mutel.org/multioutput.html>`__ for a description of multiputput process math in LCA).

    """

    use_keys = True

    def __init__(self):
        self.lca: Optional[LCA] = None
        self.score: Optional[Real] = None
        self.supply: Optional[np.array] = None
        self.cb: Optional[np.array] = None
        self.rev_act: Optional[Dict] = None
        self.rev_bio: Optional[Dict] = None
        self.fu_amount: Optional[Real] = None
        self.edge_list: Optional[GTEdgeList] = None
        self.node_list: Optional[GTNodeSet] = None
        self.counter: Optional[int] = None

    def reset(self, demand, method) -> GTTechnosphereNode:

        number_acts_in_fu = len(demand)
        if number_acts_in_fu > 1:
            raise ValueError(
                "Number activities in functional unit must be one. Aborting."
            )

        self.lca = LCA(demand, method)
        self.lca.lci()
        self.lca.lcia()
        self.score = self.lca.score

        if self.score == 0:
            raise ValueError("Zero total LCA score makes traversal impossible")

        # calculate technosphere life cycle inventory
        self.lca.decompose_technosphere()
        self.supply = self.lca.solve_linear_system()

        # Create matrix of LCIA CFs times biosphere flows, as these don't
        # change. This is also the unit score of each activity.
        self.cb = np.array(
            (self.lca.characterization_matrix * self.lca.biosphere_matrix).sum(
                axis=0
            )
        ).ravel()

        # make lookup dictionaries: matrix index -> activity key
        self.rev_act, _, self.rev_bio = self.lca.reverse_dict()

        # initialize node list
        root = GTNode(index=-1, amount=1, cum=self.score, ind=0)
        self.node_list = GTNodeSet()
        self.node_list.add(root)

        # add functional unit node
        fu_key = list(demand.keys())[0]
        fu_index = self.lca.activity_dict[fu_key]
        fu_node = GTTechnosphereNode(
            index=fu_index, lca=self.lca, cb=self.cb, supply=self.supply
        )
        self.node_list.add(fu_node)

        # initialize edge list
        self.fu_amount = demand[fu_key]
        edge = GTEdge(
            to_node=root,
            from_node=fu_node,
            amount=self.fu_amount,
            exc_amount=self.fu_amount,
            impact=self.score,
        )
        self.edge_list = GTEdgeList([edge])

        # reset calculation counter
        self.counter = 0

        return fu_node

    def calculate(
        self,
        demand: Dict,
        method: Tuple[str, str, str],
        cutoff: Real = 0.005,
        max_depth: int = 10,
        max_calc: int = 10000,
    ) -> Dict:
        """
        Traverse the supply chain graph.

        Args:
            * *demand* (dict): The functional unit. Same format as in LCA class.
            * *method* (tuple): LCIA method. Same format as in LCA class.
            * *cutoff* (float, default=0.005): Cutoff criteria to stop LCA calculations. Relative score of total, i.e. 0.005 will cutoff if a dataset has a score less than 0.5 percent of the total.
            * *max_depth* (int, default=10): Maximum depth of the iteration.
            * *max_calc* (int, default=10000): Maximum number of LCA calculation to perform.

        Returns:
            Dictionary of nodes, edges, LCA object, and number of LCA calculations.

        """

        fu_node = self.reset(demand, method)

        self.traverse(
            to_node=fu_node,
            to_amount=self.fu_amount,
            depth=0,
            max_depth=max_depth,
            max_calc=max_calc,
            abs_cutoff=abs(cutoff * self.score),
        )

        # filter nodes: only keep nodes contained in edge list
        keep_nodes = self.edge_list.get_unique_nodes()
        self.node_list.nodes = keep_nodes

        # add node.key information
        use_keys = GraphTraversal.use_keys
        if use_keys:
            self.node_list.add_node_keys(self.rev_act, self.rev_bio)

        return {
            "nodes": self.node_list.to_dict(use_keys),
            "edges": self.edge_list.to_list(use_keys),
            "lca": self.lca,
            "counter": self.counter,
        }

    def _add_biosphere_node(self, index: int) -> GTBiosphereNode:
        node = GTBiosphereNode(
            index=index, lca=self.lca, fu_amount=self.fu_amount
        )
        self.node_list.add(node)
        return node

    def _add_technosphere_node(self, index: int) -> GTTechnosphereNode:
        node = GTTechnosphereNode(
            index=index,
            lca=self.lca,
            cb=self.cb,
            supply=self.supply,
        )
        self.node_list.add(node)
        return node

    def _add_edge_if_above_cutoff(
        self,
        from_index: int,
        from_amount: Real,
        to_node: GTNode,
        to_amount: Real,
        abs_cutoff: Real,
        scaled_score_func: Callable,
        node_add_func: Callable,
    ) -> Optional[GTEdge]:
        from_node = self.node_list.get_by_index(from_index)
        if not from_node:
            from_node = node_add_func(from_index)
        amount = to_amount * from_amount
        self.counter += 1
        scaled_impact = scaled_score_func(self=from_node, factor=amount)
        if abs(scaled_impact) > abs_cutoff:
            edge = GTEdge(
                to_node, from_node, amount, from_amount, scaled_impact
            )
            self.edge_list.append(edge)
            return edge
        else:
            return None

    def traverse(
        self,
        to_node: GTNode,
        to_amount: Real,
        depth: int,
        max_depth: int,
        max_calc: int,
        abs_cutoff: Real,
    ) -> None:

        if depth >= max_depth:
            print(f"Max. depth reached at activity id {to_node.index}")
            return

        if self.counter >= max_calc:
            print(
                f"Max. number calculations reached at activity id {to_node.index}"
            )
            return

        scale_value = self.lca.technosphere_matrix[
            to_node.index, to_node.index
        ]

        # add biosphere contributions
        bio_inputs = self.lca.biosphere_matrix[:, to_node.index] / scale_value
        indices = bio_inputs.nonzero()[0]
        scaled_score_func = partial(GTBiosphereNode.scaled_score, lca=self.lca)
        for from_index, from_amount in zip(indices, bio_inputs[indices].data):
            self._add_edge_if_above_cutoff(
                from_index=from_index,
                from_amount=from_amount,
                to_node=to_node,
                to_amount=to_amount,
                abs_cutoff=abs_cutoff,
                scaled_score_func=scaled_score_func,
                node_add_func=self._add_biosphere_node,
            )

        # add technosphere contributions
        techno_inputs = (
            -1 * self.lca.technosphere_matrix[:, to_node.index] / scale_value
        )
        indices = techno_inputs.nonzero()[0]
        scaled_score_func = partial(
            GTTechnosphereNode.scaled_score, lca=self.lca, cb=self.cb
        )
        for from_index, from_amount in zip(
            indices, techno_inputs[indices].data
        ):
            # skip diagonal entries
            if from_index == to_node.index:
                continue

            edge = self._add_edge_if_above_cutoff(
                from_index=from_index,
                from_amount=from_amount,
                to_node=to_node,
                to_amount=to_amount,
                abs_cutoff=abs_cutoff,
                scaled_score_func=scaled_score_func,
                node_add_func=self._add_technosphere_node,
            )

            # continue with edge source if edge is above cutoff
            if edge:
                self.traverse(
                    to_node=edge.from_node,
                    to_amount=edge.amount,
                    depth=depth + 1,
                    max_depth=max_depth,
                    max_calc=max_calc,
                    abs_cutoff=abs_cutoff,
                )
