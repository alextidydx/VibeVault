import React from 'react';
import { useLocation, useNavigate, Link } from "react-router-dom";
import $ from "jquery";
import queryString from 'query-string';
import axios from 'axios';
import classNames from "classnames";

import '../../../styles/ui/home.scss';
import '../../../styles/ui/at-image-search.scss';

import AtWork from './../ui/AtWork.jsx';
import AtPreview from './../ui/AtPreview.jsx';
import { BASE } from '../../utils/basePath.js';

export default (props) => (
	<Home {...props} history={useLocation()} navigate={useNavigate()} />
);

class Home extends React.Component {
	container = React.createRef();
	preview = React.createRef();
	previewActive = false;
	previewCurrent = null;
	inputCont = null;
	currentURL = null;
	justLoaded = true;
	viewRect = {
		scrollTop: 0,
		scrollLeft: 0,
		viewHeight: 0,
		viewWidth: 0,
		height: 0,
		width: 0
	};

	data = {
		images: [],
		offset: 0,
		pageSize: 24,
		xhr: null
	};

	state = {
		images: [],
		isDraggingOver: false,
		queryImagePreview: null
	};

	constructor(props) {
		super(props);
	}

	componentDidMount() {
		this.inputCont = $(this.container.current).find(".e__results-form__input");
		this.currentURL = this.props.history;
		$(window).scroll(this.onScroll);
		this.checkSlug();

		$(window).on('dragover', this.handleGlobalDragOver);
		$(window).on('dragleave', this.handleGlobalDragLeave);
		$(window).on('drop', this.handleGlobalDrop);
	}

	componentDidUpdate() {
		if (this.currentURL !== this.props.history) {
			this.checkSlug();
			this.currentURL = this.props.history;
		}
	}

	componentWillUnmount() {
		$(window).off('dragover', this.handleGlobalDragOver);
		$(window).off('dragleave', this.handleGlobalDragLeave);
		$(window).off('drop', this.handleGlobalDrop);
		$(window).off('scroll', this.onScroll);
	}

	getNextPage = () => {
		if (this.data.xhr) return 0;
		if (this.data.offset > this.data.images.length - 1) {
			$(document.body).addClass("at__body--no-loading");
			return 0;
		}

		let startIndex = Math.min(this.data.offset, this.data.images.length);
		let endIndex = Math.min(startIndex + this.data.pageSize, this.data.images.length - 1);
		let ids = this.data.images.slice(startIndex, endIndex).map(object => object.id);

		if ((startIndex === endIndex) && (this.data.images.length > 0) && (startIndex > 0)) {
			ids = this.data.images.slice(-1).map(object => object.id);
		}

		this.getDetails(ids);
	}

	sortByIds = (objects, idOrder) => {
		const idMap = new Map(objects.map(obj => [obj.id, obj]));
		return idOrder
			.filter(obj => idMap.has(obj.id))
			.map(obj => {
				let elem = idMap.get(obj.id);
				if (obj.similarity) {
					elem.similarity = obj.similarity;
				}
				return elem;
			});
	};

	getDetails = (idsToLoad) => {
		if (this.data.xhr) return 0;
		if (idsToLoad.length === 0) return 0;

		this.data.xhr = axios({
			method: 'get',
			url: BASE + 'api/get-details/',
			params: {
				ids: idsToLoad,
				fields: ["desc", "content_type", "custom_modified", "rating", "last_modified"]
			}
		}).then((response) => {
			this.data.xhr = null;
			if (response) {
				let details = response.data.images.map((img) => ({
					id: img.id,
					filename: img.filename,
					desc: img.desc,
					content_type: img.content_type,
					custom_modified: img.custom_modified,
					rating: img.rating,
					last_modified: img.last_modified
				}));

				let startIndex = Math.min(this.data.offset, this.data.images.length - 1);
				let endIndex = Math.min(startIndex + this.data.pageSize, this.data.images.length - 1);
				let idOrder = this.data.images.slice(startIndex, endIndex);

				if ((startIndex === endIndex) && (this.data.images.length > 0) && (startIndex > 0)) {
					idOrder = this.data.images.slice(-1);
				}

				let sortedDetails = this.sortByIds(details, idOrder);
				let newItems = [...this.state.images, ...sortedDetails];
				this.data.offset = newItems.length;
				this.setState({ images: newItems });
			}
		}).catch((error) => {
			console.error("Details load failed", error);
			this.data.xhr = null;
		});
	}

	getIndices = (type, query) => {
		this.data.xhr = axios({
			method: 'get',
			url: BASE + 'api/' + type + "/",
			params: {
				query: query
			}
		}).then((response) => {
			this.data.xhr = null;
			if (response) {
				window.scrollTo({ top: 0, behavior: 'smooth' });
				this.data.images = response.data.images;
				this.data.offset = 0;
				this.setState({ images: [] });

				$(document.body).removeClass("at__body--no-loading");
				this.getNextPage();
			}
		}).catch((error) => {
			console.error("Image index load failed", error);
			this.data.xhr = null;
		});
	}

	recheck = () => {
		axios({
			method: 'get',
			url: BASE + 'api/recheck/'
		}).catch((error) => {
			console.error("Library refresh failed", error);
		});
	}

	handleGlobalDragOver = (e) => {
		e.preventDefault();
		this.setState({ isDraggingOver: true });
	};

	handleGlobalDragLeave = (e) => {
		this.setState({ isDraggingOver: false });
	};

	handleGlobalDrop = (e) => {
		e.preventDefault();
		this.setState({ isDraggingOver: false });

		const file = e.originalEvent.dataTransfer.files[0];
		if (!file) return;

		const allowedTypes = [
			'image/jpeg',
			'image/jpg',
			'image/png',
			'image/webp',
			'image/jfif'
		];

		const allowedExtensions = ['.jpg', '.jpeg', '.png', '.webp', '.jfif'];
		const fileName = file.name.toLowerCase();
		const hasValidExtension = allowedExtensions.some(ext => fileName.endsWith(ext));

		if (allowedTypes.includes(file.type) || hasValidExtension) {
			this.processDroppedImage(file);
		}
	};

	processDroppedImage = (file) => {
		const previewUrl = URL.createObjectURL(file);

		this.setState({
			queryImagePreview: previewUrl
		});

		this.props.navigate(BASE + 'search-image/');
		this.searchImageBackend(file);
	};

	searchImageBackend = async (file) => {
		const formData = new FormData();
		formData.append('image', file);

		this.data.xhr = axios({
			method: 'POST',
			url: BASE + 'api/search-image/',
			data: formData,
			headers: {
				'Content-Type': 'multipart/form-data'
			}
		}).then((response) => {
			this.data.xhr = null;
			if (response) {
				window.scrollTo({ top: 0, behavior: 'smooth' });
				this.data.images = response.data.images;
				this.data.offset = 0;
				this.setState({ images: [] });
				$(document.body).removeClass("at__body--no-loading");
				this.getNextPage();
			}
		}).catch((error) => {
			console.error("Image search failed", error);
			this.data.xhr = null;
		});
	};

	clearQueryImage = () => {
		if (this.state.queryImagePreview) {
			URL.revokeObjectURL(this.state.queryImagePreview);
		}
		this.setState({
			queryImagePreview: null
		});
		this.props.navigate(BASE);
	};

	showDesc = (work) => {
		this.previewCurrent = work;
		this.preview.current.showDesc(work);
		this.previewActive = true;
		this.lock();
	}

	hideDesc = () => {
		this.previewActive = false;
		this.unlock();
		this.previewCurrent = null;
	}

	lock = () => {
		$(document.body).addClass("at__body--locked");
	}

	unlock = () => {
		$(document.body).removeClass("at__body--locked");
	}

	onScroll = () => {
		this.viewRect = {
			scrollTop: $(window).scrollTop(),
			scrollLeft: $(window).scrollLeft(),
			viewHeight: $(window).height(),
			viewWidth: $(window).width(),
			height: $(document).height(),
			width: $(document).height(),
		};

		if (this.viewRect.scrollTop <= 0) {
			$("body").removeClass("e__body--shadow");
		}
		if (this.viewRect.scrollTop > 0) {
			$("body").addClass("e__body--shadow");
		}

		let shouldLoadMore = (this.viewRect.height - (this.viewRect.scrollTop + this.viewRect.viewHeight)) < this.viewRect.viewHeight / 2 && this.data.xhr == null;
		if (shouldLoadMore) {
			this.getNextPage();
		}
	}

	checkSlug = () => {
		let parsed = queryString.parse(this.props.history.search);
		this.inputCont.val(parsed.query);
		if (this.justLoaded) {
			this.justLoaded = false;
		}
		if (this.props.history.pathname.indexOf("/search/") > -1) {
			this.getIndices("search", parsed.query);
			return 0;
		}

		this.getIndices("recent");
	}

	search = () => {
		if (this.data.xhr) return 0;

		let query = this.inputCont.val();
		this.inputCont.blur();
		let stringified = queryString.stringify({ query: query });
		this.props.navigate(BASE + "search/?" + stringified);
	}

	handleKeyDown = (e) => {
		if (e.keyCode === 13) {
			this.search();
		}
	}

	render() {
		const classnames = classNames({
			"e__results-bar": true,
			"e__results-bar--loading": this.state.images.length > this.data.offset,
			"e__results-bar--image-search": this.state.queryImagePreview,
			"e__results-bar--dragging": this.state.isDraggingOver
		});

		return (
			<div className={classnames} ref={this.container}>
				<div className="e__results-block">
					{
						(this.state.images && (this.state.images.length > 0)) ?
							this.state.images.map((img) => {
								return <AtWork data={img} key={img.id} showDescInput={this.showDesc} />
							})
						:
							<p>Nothing to show</p>
					}
				</div>

				<div className="e__results-title">
					<Link className="e__results-form__btn-logo" to={BASE} ></Link>
					<form className="e__results-form" action={BASE + "search/"} onSubmit={e => { e.preventDefault(); }}>
						<div className="e__image-dd__query-image-block">
							<button className="e__image-dd__query-delete-btn" onClick={this.clearQueryImage}>x</button>
							<img
								src={this.state.queryImagePreview}
								className="e__image-dd__query-small-preview"
								alt="Search source"
							/>
						</div>
						<input className="e__results-form__input" type="text" id="query" name="query" placeholder="Get lucky" onKeyDown={this.handleKeyDown} autoComplete="search" />
						<button className="e__results-form__btn" type="submit" onClick={this.search}>Search</button>
					</form>
					<button className="e__results-form__btn-refresh secondary" type="submit" onClick={this.recheck}>Refresh</button>
				</div>

				<AtPreview ref={this.preview} hideW={this.hideDesc} />

				<div className="e__image-dd__overlay">
					<div className="e__image-dd__overlay-text ">Drop image here</div>
				</div>
			</div>
		);
	}
}
